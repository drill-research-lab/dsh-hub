import os
import json
import ssl
import sys

import ldap3
from ldap3.utils.conv import escape_filter_chars
import redis.asyncio as redis_asyncio

sys.path.insert(0, "/srv/jupyterhub")
from api_keys import ApiKeyStore, AUTO_PROVISIONED_LABEL
from resource_limits import ResourceLimitStore, DEFAULT_CPU_CORES, DEFAULT_MEMORY_MB, DEFAULT_DISK_MB

c = get_config()
c.JupyterHub.bind_url = "http://0.0.0.0:8000"
c.JupyterHub.hub_ip = "0.0.0.0"
c.JupyterHub.hub_connect_ip = "hub"
c.JupyterHub.db_url = "sqlite:////data/jupyterhub.sqlite"
c.JupyterHub.cookie_secret_file = "/data/jupyterhub_ldap_cookie_secret"
c.JupyterHub.template_paths = ["/srv/jupyterhub/templates"]
c.JupyterHub.logo_file = "/srv/jupyterhub/static/logo.png"
# LDAP is required; there is no fallback to test authentication.
def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} in .env before starting JupyterHub")
    return value

c.JupyterHub.authenticator_class = "ldapauthenticator.LDAPAuthenticator"
c.Authenticator.allow_existing_users = False
c.LDAPAuthenticator.server_address = required("LDAP_SERVER_ADDRESS")
c.LDAPAuthenticator.tls_strategy = os.environ.get("LDAP_TLS_STRATEGY", "before_bind")
if c.LDAPAuthenticator.tls_strategy not in {"before_bind", "on_connect", "insecure"}:
    raise ValueError("LDAP_TLS_STRATEGY must be before_bind, on_connect, or insecure")
default_port = "636" if c.LDAPAuthenticator.tls_strategy == "on_connect" else "389"
c.LDAPAuthenticator.server_port = int(os.environ.get("LDAP_SERVER_PORT") or default_port)
c.LDAPAuthenticator.tls_kwargs = {"validate": ssl.CERT_REQUIRED}
if os.environ.get("LDAP_CA_CERT_FILE"):
    c.LDAPAuthenticator.tls_kwargs["ca_certs_file"] = os.environ["LDAP_CA_CERT_FILE"]
c.LDAPAuthenticator.lookup_dn = True
c.LDAPAuthenticator.user_search_base = required("LDAP_USER_SEARCH_BASE")
c.LDAPAuthenticator.user_attribute = os.environ.get("LDAP_USER_ATTRIBUTE", "uid")
c.LDAPAuthenticator.lookup_dn_user_dn_attribute = os.environ.get("LDAP_USER_DN_ATTRIBUTE", "cn")
c.LDAPAuthenticator.lookup_dn_search_filter = "({login_attr}={login})"
c.LDAPAuthenticator.lookup_dn_search_user = os.environ.get("LDAP_BIND_DN") or None
c.LDAPAuthenticator.lookup_dn_search_password = os.environ.get("LDAP_BIND_PASSWORD") or None
if bool(c.LDAPAuthenticator.lookup_dn_search_user) != bool(c.LDAPAuthenticator.lookup_dn_search_password):
    raise ValueError("LDAP_BIND_DN and LDAP_BIND_PASSWORD must both be set, or both empty for anonymous search")
c.LDAPAuthenticator.use_lookup_dn_username = False
# Default ldapauthenticator regex (^[a-z][.a-z0-9_-]*$) requires a letter
# first and rejects this LLDAP's real usernames, which are student/staff ID
# style and start with a digit (e.g. 61447007s). ldap3 already escapes
# special characters against LDAP injection, so this is just a sanity check
# now, not the injection defense it originally was -- widen it to also allow
# a leading digit instead of loosening it further than necessary.
c.LDAPAuthenticator.valid_username_regex = r"^[a-zA-Z0-9][.a-zA-Z0-9_-]*$"
groups = json.loads(os.environ.get("LDAP_ALLOWED_GROUPS", "[]"))
if not isinstance(groups, list) or any(not isinstance(g, str) or not g.strip() for g in groups):
    raise ValueError("LDAP_ALLOWED_GROUPS must be a JSON array of group DNs")
c.LDAPAuthenticator.allowed_groups = groups
c.LDAPAuthenticator.group_search_filter = "(member={userdn})"
c.LDAPAuthenticator.group_attributes = ["member"]
c.Authenticator.allow_all = not bool(groups)

# Admin group is separate from the login-allowed groups above: membership here
# only sets the JupyterHub admin flag, it never gates login. Leave
# LDAP_ADMIN_GROUP empty until the group DN exists in LLDAP; no one is admin
# until it's filled in.
admin_group = os.environ.get("LDAP_ADMIN_GROUP", "").strip()


def _admin_group_post_auth_hook(authenticator, handler, auth_model):
    # Every branch below sets auth_model["admin"] explicitly (True or False),
    # never leaves it unset. JupyterHub only touches the persisted user.admin
    # column when the hook returns a non-None value (see auth_to_user() in
    # jupyterhub/handlers/base.py), so leaving it unset on early-return paths
    # (no admin group configured, LDAP lookup failed, ...) would silently
    # keep whatever admin flag that user happened to have from a *previous*
    # login instead of re-deriving it -- caught by testing this against a
    # real LDAP group and finding a user stayed admin after the group was
    # unset again (see STATUS.md).
    if not admin_group:
        auth_model["admin"] = False
        return auth_model
    username = auth_model["name"]
    _, userdn = authenticator.resolve_username(username)
    if not userdn:
        authenticator.log.error("Admin group check: could not resolve DN for %s", username)
        auth_model["admin"] = False
        return auth_model
    conn = authenticator.get_connection(
        authenticator.lookup_dn_search_user, authenticator.lookup_dn_search_password
    )
    if not conn:
        authenticator.log.error("Admin group check: failed to bind service account")
        auth_model["admin"] = False
        return auth_model
    is_admin = conn.search(
        search_base=admin_group,
        search_scope=ldap3.BASE,
        search_filter=authenticator.group_search_filter.format(
            userdn=escape_filter_chars(userdn), uid=escape_filter_chars(username)
        ),
        attributes=authenticator.group_attributes,
    )
    auth_model["admin"] = bool(is_admin)
    return auth_model


c.Authenticator.post_auth_hook = _admin_group_post_auth_hook

c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
c.DockerSpawner.image = "dsh-demo-user:local"
c.DockerSpawner.pull_policy = "never"
c.DockerSpawner.network_name = os.environ.get("DOCKER_NETWORK_NAME", "dsh-demo")
c.DockerSpawner.use_internal_ip = True
# Containers persist across restarts: stopped, not removed, and the Hub never
# tears them down on its own shutdown/restart. Home directories live in a
# per-user named volume so data survives container recreation.
c.DockerSpawner.remove = False
c.JupyterHub.cleanup_servers = False
c.DockerSpawner.volumes = {"dsh-demo-home-{username}": "/home/demo"}
c.DockerSpawner.name_template = "dsh-demo-{username}"
# Deny-by-default capabilities. cap_add is intentionally empty: verified via a
# live spawn that dsh's startup/auth path needs none (see STATUS.md); add one
# back only if a real container later shows "Operation not permitted"/
# "permission denied" tracing to a missing capability.
c.DockerSpawner.extra_host_config = {
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
}
c.Spawner.default_url = "/harness/"
c.Spawner.http_timeout = 120
c.Spawner.start_timeout = 120
c.JupyterHub.shutdown_on_logout = False

# Every container gets a real Dispatcher API key baked in at spawn time --
# this is the identity mechanism from design doc section 7, not a
# self-reported username the container's own user could forge.
#
# The key can only be injected at container *creation*: Docker does not let
# `docker start` apply new -e values to a container that already exists, so
# a real new container needs a freshly minted key every time, while
# resuming an existing (remove=False) one must not touch env vars at all.
# This must gate on whether *this exact container* currently exists in
# Docker (spawner.get_object()), not on whether Redis already has a key on
# file for this user -- those can disagree, e.g. right after the container
# was deleted out-of-band while its key record was still in Redis, which is
# exactly the bug this replaced: the old key never got re-injected into the
# rebuilt container, so it silently ran with no DISPATCHER_API_KEY at all.
_api_key_redis = redis_asyncio.from_url(
    os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True
)
_api_keys = ApiKeyStore(_api_key_redis)

# Per-user CPU/memory limits (design doc section 3). Same Redis instance as
# the API keys above; an admin sets these through Panel, which also applies
# them live to a running container via `docker update` -- this store is only
# consulted here for a container that's actually about to be *created*.
_resource_limits = ResourceLimitStore(
    _api_key_redis,
    default_cpu=float(os.environ.get("DEFAULT_CPU_CORES", DEFAULT_CPU_CORES)),
    default_memory_mb=int(os.environ.get("DEFAULT_MEMORY_MB", DEFAULT_MEMORY_MB)),
    default_disk_mb=int(os.environ.get("DEFAULT_DISK_MB", DEFAULT_DISK_MB)),
)


async def _pre_spawn_hook(spawner):
    spawner.environment["DISPATCHER_BASE_URL"] = "http://dispatcher:8080/v1"
    existing_container = await spawner.get_object()
    if existing_container is None:
        await _api_keys.revoke_auto_keys(spawner.user.name)
        raw_key, _meta = await _api_keys.issue(
            spawner.user.name, label=AUTO_PROVISIONED_LABEL, issued_by="system"
        )
        spawner.environment["DISPATCHER_API_KEY"] = raw_key

    # mem_limit/cpu_limit only take effect inside create_object(), which
    # DockerSpawner only calls for a genuinely new container (an existing,
    # merely-restarting one skips straight to `docker start` and never reads
    # these) -- setting them unconditionally here is harmless for the resume
    # case, just unused. Changing a limit on a container that already exists
    # needs a live `docker update`, which is what Panel's resource-limits
    # endpoint does instead of forcing a respawn.
    limit = await _resource_limits.get(spawner.user.name)
    spawner.mem_limit = f"{limit['memory_mb']}M"
    spawner.cpu_limit = limit["cpu"]


c.Spawner.pre_spawn_hook = _pre_spawn_hook

# Queue admin panel, run as its own compose service (panel/), reverse-proxied
# by the Hub at /services/panel/ with SSO via Hub OAuth. Not spawned by the
# Hub itself, so api_token/url are wired to match panel's own env vars in
# compose.yaml instead of being auto-generated.
c.JupyterHub.services = [
    {
        "name": "panel",
        "url": "http://panel:8090",
        "api_token": required("PANEL_API_TOKEN"),
        "display": True,
        # Skip the OAuth "authorize access" confirmation screen: panel is a
        # first-party part of this deployment, not a third-party app, so the
        # extra click would just be friction for every login.
        "oauth_no_confirm": True,
    }
]
# The default "user" role only grants the "self" scope, which does not
# include permission to complete OAuth against a service -- every user needs
# to at least *reach* the panel (it enforces its own read-only-vs-admin
# distinction internally, based on the Hub admin flag from the LDAP
# admin-group hook above). Scopes here replace the role's scope list
# entirely, so "self" must be re-listed or normal user functionality breaks.
c.JupyterHub.load_roles = [
    {
        "name": "user",
        "scopes": ["self", "access:services!service=panel"],
    }
]
