# OpenShell Dashboard Helm chart

Deploy the dashboard's React frontend and Go backend in one container, optionally
with an oauth2-proxy sidecar. The OpenShell gateway and identity provider are
installed and managed separately. The chart creates no identity provider, OIDC
client registration, or credential Secret.

Requires Helm 3 and Kubernetes 1.25+. Select a dashboard image compatible with
your gateway using the repository's [compatibility table](../../../../README.md#compatibility).
`image.tag` or `image.digest` and `gateway.url` are required; there is no implicit
`latest` image or assumed gateway release name.

## Install with an existing identity provider

The default `auth.mode: oauth2-proxy` uses generic OIDC. Register a confidential
client with your existing provider, enabling the authorization code flow with
PKCE S256 and the redirect URI `https://<dashboard-host>/oauth2/callback`.
Configure the gateway to accept the forwarded token's issuer, audience, and
role claims. The gateway audience is not necessarily the proxy's client ID.

Provide existing Secrets in the release namespace:

- An oauth2-proxy Secret with `client-secret` and `cookie-secret` keys. Use a
  base64-encoded random 32-byte cookie secret; share it across replicas. See
  [oauth2-proxy's configuration guide](https://oauth2-proxy.github.io/oauth2-proxy/7.9.x/configuration/overview/).
- A TLS Secret for the dashboard ingress certificate.
- If the gateway uses a private CA, a Secret with `ca.crt`. Include `tls.crt`
  and `tls.key` only if the connection also requires a client certificate.

Keep credentials out of values files and `--set` arguments. Provision these
Secrets through your existing secret-management process before installing.

Create `dashboard-values.yaml` (replace the example addresses and image version):

```yaml
image:
  tag: "1.1.1" # Example only: check compatibility with your gateway.
gateway:
  url: https://openshell.openshell.svc:8080
  tls:
    existingSecret: openshell-gateway-ca
oauth2Proxy:
  issuerUrl: https://idp.example.com/realms/openshell
  clientId: openshell-dashboard
  redirectUrl: https://dashboard.example.com/oauth2/callback
  existingSecret: dashboard-oauth2
  emailDomains: [example.com]
ingress:
  enabled: true
  className: nginx
  host: dashboard.example.com
  tlsSecretName: dashboard-tls
```

From a checkout of this repository:

```shell
helm upgrade --install dashboard ./deploy/helm/openshell-dashboard \
  --namespace openshell --create-namespace \
  --values dashboard-values.yaml --wait --timeout 5m
```

The Service routes exclusively to oauth2-proxy on port 4180. The dashboard binds
to `127.0.0.1:8080` inside the pod, so other pods cannot bypass the proxy by
addressing the dashboard port. TLS terminates at your ingress controller; set
its annotations as needed for HTTPS redirects and long-lived WebSockets used
by sandbox terminals. To use another external TLS entry point, leave
`ingress.enabled: false` and route it to the chart's Service.

The bundled proxy forwards the OAuth access token through
`X-Forwarded-Access-Token`, replacing any caller-supplied value. The gateway must
accept that token's issuer, audience, and claims; an opaque access token cannot
be validated as an OIDC JWT. Configure `scope` to request any additional claims
your provider requires. If your provider requires forwarding an ID token or
exchanging tokens, use external proxy mode and configure that proxy to inject
the gateway-compatible token in the BFF's configured token header.

For a private issuer CA, set `oauth2Proxy.providerCA.existingSecret` and optionally
`providerCA.key`. This trust bundle is separate from `gateway.tls`. The chart
does not disable issuer or certificate verification. Provider-specific login
flows beyond these generic OIDC settings can use external proxy mode.

## Use an existing authentication proxy

Set `auth.mode: external` to omit oauth2-proxy and all its OIDC configuration.
Your existing proxy owns login, sessions, refresh, logout, CSRF protection, and
WebSocket authentication. It must strip caller-supplied authentication headers
and inject the authenticated user's token on every upstream request.

```yaml
auth:
  mode: external
  logoutUrl: /your-proxy/logout
  external:
    tokenHeader: x-forwarded-access-token
    userHeader: x-auth-request-user
    allowedPeers:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: authentication
        podSelector:
          matchLabels:
            app.kubernetes.io/name: trusted-auth-proxy
```

This mode creates a ClusterIP Service and an ingress NetworkPolicy allowing
only the selected proxy pods to connect to port 8080. The namespace and pod
selectors in the example are in the same peer, so both must match. Omitting
`namespaceSelector` restricts the peer to the release namespace.

Your CNI must enforce NetworkPolicy. Kubernetes policies are additive: ensure
no other policy grants broader access to these dashboard pods. The chart
rejects an empty peer list and disables its own Ingress in this mode; public
routing belongs to the external proxy. Direct browser access to the Service
does not provide a login flow. See [ADR 0002](../../../../docs/adrs/0002-auth-relay-only-bff.md).

## Local development

Disable authentication explicitly and use a gateway configured for local
development. The dashboard does not forward a user token in this mode.

```shell
helm upgrade --install dashboard ./deploy/helm/openshell-dashboard \
  --namespace openshell --create-namespace \
  --set auth.mode=development \
  --set-string image.tag=1.1.1 \
  --set gateway.url=http://openshell.openshell.svc:8080 \
  --wait --timeout 5m
kubectl -n openshell port-forward service/dashboard-openshell-dashboard 8080:80
```

Open `http://localhost:8080`. Do not expose this mode on shared or public networks.

## Configuration and lifecycle

All settings are documented in [values.yaml](values.yaml). Common options:

| Value | Purpose |
| --- | --- |
| `image.repository`, `image.tag`, `image.digest` | Dashboard image; digest takes precedence over tag |
| `gateway.url` | Existing gateway's administrative gRPC endpoint, reachable from the pod |
| `gateway.tls.existingSecret`, `caKey` | Gateway CA trust; omit for a publicly trusted certificate |
| `gateway.tls.clientCertKey`, `clientKeyKey` | Optional mTLS key names, supplied together |
| `auth.mode` | `oauth2-proxy` (default), `external`, or `development` |
| `oauth2Proxy.existingSecret` | Existing proxy client and cookie credentials |
| `oauth2Proxy.emailDomains` | Required email domain allowlist; explicitly set `["*"]` to allow all |
| `oauth2Proxy.cookieRefresh` | Session refresh interval, default `5m`; choose below token expiry |
| `service.port` | ClusterIP service port, default `80` |
| `ingress.*` | Optional HTTPS ingress at `/`; external mode manages routing separately |
| `replicaCount`, `resources`, `oauth2Proxy.resources` | Capacity and resource requests/limits |
| `imagePullSecrets`, `nodeSelector`, `tolerations`, `affinity` | Cluster placement and image access |
| `podSecurityContext`, `securityContext` | Non-root execution, dropped capabilities, read-only root filesystem |

The dashboard image must include BusyBox `wget` for localhost health probes,
as the repository's Alpine image does. Liveness checks only the BFF; readiness
also checks gateway connectivity. A ready pod does not prove browser login or
gateway authorization: verify login and a workspace operation after installing.

Use the same `helm upgrade --install` command for subsequent upgrades, pinning
a tested dashboard/gateway pair. Gateway releases are upgraded independently.
After rotating externally managed credentials or certificates, restart the
Deployment so environment variables and TLS clients reload. `helm uninstall
dashboard -n openshell` removes chart resources while preserving your existing
Secrets, gateway, and identity provider.

This chart is installed from source; this change does not publish a Helm/OCI
artifact. Install scripts can pin a repository revision and use its chart path,
or consume a chart archive produced by:

```shell
helm package deploy/helm/openshell-dashboard --destination dist
```

## Validate changes

```shell
make test-helm
```

Requires Helm 3 and `uv`. The suite lints, packages, and renders all three modes,
checks proxy isolation and Secret/TLS wiring, and verifies invalid configurations
fail before installation. Fixtures in `ci/` contain example names only. CI runs
the same suite; it does not require an identity provider or Kubernetes cluster.
