{{/*
_helpers.tpl - small reusable template snippets ("named templates").
Files starting with "_" render nothing themselves; other templates call these with
  {{ include "vigil.labels" (dict "root" . "component" "api") }}
*/}}

{{/* Chart name + version, e.g. "vigil-0.1.0" (for the helm.sh/chart label). */}}
{{- define "vigil.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Selector labels: the SMALL, never-changing set a Deployment/StatefulSet/Service uses
to find its pods. (A selector cannot be changed after creation, so no version here.)
Usage: include "vigil.selectorLabels" (dict "root" . "component" "api")
*/}}
{{- define "vigil.selectorLabels" -}}
app.kubernetes.io/name: vigil
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Full labels for every object: selector labels + chart/app version + who manages it.
The NetworkPolicies match pods on "app.kubernetes.io/component".
*/}}
{{- define "vigil.labels" -}}
{{ include "vigil.selectorLabels" . }}
app.kubernetes.io/part-of: vigil
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ include "vigil.chart" .root }}
{{- with .root.Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Image reference from an image block {registry, repository, tag}:
  registry ""        -> "vigil-core:dev"
  registry ghcr.io   -> "ghcr.io/gagan046-dev/vigil-core:0.1.0"
An empty tag falls back to the chart's appVersion.
Usage: include "vigil.image" (dict "root" . "image" .Values.api.image)
*/}}
{{- define "vigil.image" -}}
{{- $tag := .image.tag | default .root.Chart.AppVersion | toString -}}
{{- if .image.registry -}}
{{- printf "%s/%s:%s" .image.registry .image.repository $tag -}}
{{- else -}}
{{- printf "%s:%s" .image.repository $tag -}}
{{- end -}}
{{- end -}}

{{/*
Pod-level securityContext that satisfies the Pod Security Standard "restricted":
non-root user, seccomp RuntimeDefault. Usage: include "vigil.podSecurity" (dict "uid" 10001)
*/}}
{{- define "vigil.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .gid | default .uid }}
{{- if .fsGroup }}
fsGroup: {{ .fsGroup }}
{{- end }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Container-level securityContext ("restricted"): no escalation, no capabilities, read-only root. */}}
{{- define "vigil.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: {{ if hasKey . "readOnly" }}{{ .readOnly }}{{ else }}true{{ end }}
capabilities:
  drop: ["ALL"]
{{- end -}}

{{/* imagePullSecrets block (renders nothing when the list is empty). */}}
{{- define "vigil.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Pod annotations that make pods RESTART when their configuration changes.
Kubernetes only rolls a Deployment when the pod template changes; a changed ConfigMap
alone does not. So we put a hash (sha256) of the rendered ConfigMap/Secret into the
pod template: new config -> new hash -> new pod template -> rolling restart.
*/}}
{{- define "vigil.configChecksums" -}}
checksum/config: {{ include (print .Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- if .Values.secrets.create }}
checksum/secret: {{ include (print .Template.BasePath "/secret.yaml") . | sha256sum }}
{{- end }}
{{- end -}}

{{/* env entry: POSTGRES_PASSWORD read from the Secret. */}}
{{- define "vigil.envPostgresPassword" -}}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.name }}
      key: postgres-password
{{- end -}}

{{/* env entry: LLM_API_KEY (the LiteLLM master key) - optional, the pod starts without it. */}}
{{- define "vigil.envLlmKey" -}}
- name: LLM_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.name }}
      key: {{ .Values.llmGateway.masterKeySecretKey }}
      optional: true
{{- end -}}
