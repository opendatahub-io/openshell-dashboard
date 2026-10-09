{{- define "dashboard.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "dashboard.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "dashboard.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "dashboard.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dashboard.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "dashboard.labels" -}}
{{ include "dashboard.selectorLabels" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "dashboard.image" -}}
{{- if .digest -}}
{{- printf "%s@%s" .repository .digest -}}
{{- else -}}
{{- printf "%s:%s" .repository (required "image.tag (or image.digest) is required; select a gateway-compatible release" .tag) -}}
{{- end -}}
{{- end -}}

{{- define "dashboard.validate" -}}
{{- if not (has .Values.auth.mode (list "oauth2-proxy" "external" "development")) -}}
{{- fail "auth.mode must be oauth2-proxy, external, or development" -}}
{{- end -}}
{{- $_ := required "gateway.url is required" .Values.gateway.url -}}
{{- if .Values.gateway.tls.existingSecret -}}
{{- $_ := required "gateway.tls.caKey is required with existingSecret" .Values.gateway.tls.caKey -}}
{{- end -}}
{{- if ne (empty .Values.gateway.tls.clientCertKey) (empty .Values.gateway.tls.clientKeyKey) -}}
{{- fail "gateway.tls.clientCertKey and clientKeyKey must be set together" -}}
{{- end -}}
{{- if and .Values.gateway.tls.clientCertKey (not .Values.gateway.tls.existingSecret) -}}
{{- fail "gateway.tls.existingSecret is required for mTLS" -}}
{{- end -}}
{{- if eq .Values.auth.mode "oauth2-proxy" -}}
{{- if not (hasPrefix "https://" .Values.oauth2Proxy.issuerUrl) -}}
{{- fail "oauth2Proxy.issuerUrl must be an HTTPS URL for an existing identity provider" -}}
{{- end -}}
{{- if not (and (hasPrefix "https://" .Values.oauth2Proxy.redirectUrl) (hasSuffix "/oauth2/callback" .Values.oauth2Proxy.redirectUrl)) -}}
{{- fail "oauth2Proxy.redirectUrl must be https://<dashboard-host>/oauth2/callback" -}}
{{- end -}}
{{- $_ := required "oauth2Proxy.clientId is required" .Values.oauth2Proxy.clientId -}}
{{- $_ := required "oauth2Proxy.existingSecret is required" .Values.oauth2Proxy.existingSecret -}}
{{- $_ := required "oauth2Proxy.clientSecretKey is required" .Values.oauth2Proxy.clientSecretKey -}}
{{- $_ := required "oauth2Proxy.cookieSecretKey is required" .Values.oauth2Proxy.cookieSecretKey -}}
{{- if .Values.oauth2Proxy.providerCA.existingSecret -}}
{{- $_ := required "oauth2Proxy.providerCA.key is required" .Values.oauth2Proxy.providerCA.key -}}
{{- end -}}
{{- if empty .Values.oauth2Proxy.emailDomains -}}
{{- fail "oauth2Proxy.emailDomains must explicitly allow login domains" -}}
{{- end -}}
{{- end -}}
{{- if eq .Values.auth.mode "external" -}}
{{- if empty .Values.auth.external.allowedPeers -}}
{{- fail "auth.external.allowedPeers must select trusted proxy pods" -}}
{{- end -}}
{{- range .Values.auth.external.allowedPeers -}}
{{- if not (hasKey . "podSelector") -}}
{{- fail "each auth.external.allowedPeers entry must contain a podSelector" -}}
{{- end -}}
{{- if and (empty .podSelector.matchLabels) (empty .podSelector.matchExpressions) -}}
{{- fail "external proxy podSelector must not be empty" -}}
{{- end -}}
{{- if hasKey . "ipBlock" -}}
{{- fail "external proxy peers must use pod selectors, not ipBlock" -}}
{{- end -}}
{{- end -}}
{{- if .Values.ingress.enabled -}}
{{- fail "external mode requires routing through the external proxy; disable ingress.enabled" -}}
{{- end -}}
{{- end -}}
{{- if .Values.ingress.enabled -}}
{{- $_ := required "ingress.host is required" .Values.ingress.host -}}
{{- $_ := required "ingress.tlsSecretName is required" .Values.ingress.tlsSecretName -}}
{{- if and (eq .Values.auth.mode "oauth2-proxy") (ne .Values.oauth2Proxy.redirectUrl (printf "https://%s/oauth2/callback" .Values.ingress.host)) -}}
{{- fail "oauth2Proxy.redirectUrl must use ingress.host when ingress is enabled" -}}
{{- end -}}
{{- end -}}
{{- end -}}
