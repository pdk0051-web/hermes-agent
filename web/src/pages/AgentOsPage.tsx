import { useCallback, useEffect, useState, type ComponentType } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Database,
  FileCheck2,
  FileText,
  GitBranch,
  Link2,
  RefreshCw,
  Scale,
  ShieldCheck,
  Sparkles,
  Users,
  XCircle,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { api, type AgentOsStatusResponse } from "@/lib/api";
import { PluginSlot } from "@/plugins";

function shortHash(value: string | null): string {
  return value ? value.slice(0, 12) : "not available";
}

function StateBadge({ ok, label }: { ok: boolean; label: string }) {
  return <Badge tone={ok ? "success" : "destructive"}>{label}</Badge>;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 border-b border-border/60 py-2 last:border-b-0 sm:grid-cols-[9rem_1fr]">
      <dt className="text-xs text-text-tertiary">{label}</dt>
      <dd className="min-w-0 break-words font-mono-ui text-xs text-text-secondary">
        {value || "not set"}
      </dd>
    </div>
  );
}

function axisTone(status: string): "success" | "warning" | "destructive" {
  if (status === "visible") return "success";
  if (status === "partial") return "warning";
  return "destructive";
}

function preflightTone(status: string): "success" | "warning" | "destructive" {
  if (status === "passing") return "success";
  if (status === "failing") return "destructive";
  return "warning";
}

function workFrameTone(status: string): "success" | "warning" | "destructive" {
  if (status === "detected") return "success";
  if (status === "declared") return "warning";
  return "destructive";
}

function contractGateTone(status: string): "success" | "warning" | "destructive" {
  if (status === "enabled") return "warning";
  if (status === "disabled") return "destructive";
  return "warning";
}

function formatCascadeTiers(tiers: Record<string, number>): string {
  const entries = Object.entries(tiers).sort(([left], [right]) =>
    left.localeCompare(right, undefined, { numeric: true }),
  );
  return entries.length
    ? entries.map(([tier, count]) => `${tier}:${count}`).join(" / ")
    : "none";
}

function LoopAxisCard({
  title,
  icon: Icon,
  status,
  metrics,
  warnings,
}: {
  title: string;
  icon: ComponentType<{ className?: string }>;
  status: string;
  metrics: { label: string; value: string }[];
  warnings: string[];
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Icon className="h-4 w-4 text-text-secondary" />
            <CardTitle>{title}</CardTitle>
          </div>
          <Badge tone={axisTone(status)} className="normal-case">
            {status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          {metrics.map((metric) => (
            <div key={metric.label} className="min-w-0">
              <p className="text-xs text-text-tertiary">{metric.label}</p>
              <p className="mt-1 break-words font-mono-ui text-xs text-text-primary">
                {metric.value}
              </p>
            </div>
          ))}
        </div>
        {warnings.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {warnings.map((warning) => (
              <Badge key={warning} tone="warning" className="normal-case">
                {warning}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function AgentOsPage() {
  const [status, setStatus] = useState<AgentOsStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getAgentOsStatus()
      .then(setStatus)
      .catch((err: unknown) => setError(String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !status) {
    return (
      <div className="flex min-h-64 items-center justify-center text-text-secondary">
        <Spinner className="text-xl text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-start gap-3 py-4">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="min-w-0">
            <p className="text-sm text-text-primary">
              Agent OS status failed to load.
            </p>
            <p className="mt-1 break-words text-xs text-text-secondary">
              {error}
            </p>
            <Button className="mt-3" ghost onClick={load}>
              <RefreshCw className="h-4 w-4" />
              Retry
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!status) return null;

  const installedSkills = status.skills.filter((skill) => skill.installed).length;
  const readyHooks = status.bridge.required_hooks.filter(
    (hook) =>
      status.bridge.hooks_declared.includes(hook) &&
      status.bridge.hook_files[hook],
  ).length;
  const harnessedCitizens = status.citizens.filter(
    (citizen) => citizen.harnessed,
  ).length;
  const knowledgeCounts = status.loop.knowledge.counts;
  const preflightChecks = Object.entries(status.preflight.checks);
  const signedHead = status.preflight.signed_head;
  const workFrameReadyFields = status.work_frame.required_fields.filter(
    (field) => status.work_frame.field_markers[field],
  ).length;
  const workFrameApplied =
    status.work_frame.status === "detected" &&
    workFrameReadyFields === status.work_frame.required_fields.length;
  const contractGateVisibleOnly = status.contract_gate.status === "disabled";
  const preflightFailing = status.preflight.status === "failing";
  const pmObservationTone: "success" | "warning" | "destructive" =
    !status.detected || !workFrameApplied
      ? "destructive"
      : preflightFailing || contractGateVisibleOnly
        ? "warning"
        : "success";
  const pmObservationLabel =
    pmObservationTone === "success"
      ? "운영 가능"
      : pmObservationTone === "warning"
        ? "관측 가능, 정비 필요"
        : "연동 확인 필요";
  const nextGovernedAct = !workFrameApplied
    ? "작업 프레임 주입 경로를 먼저 복구한다"
    : contractGateVisibleOnly
      ? "계약 종료 조건과 실패 동작을 검증한 뒤 차단 게이트를 켠다"
      : preflightFailing
        ? "LEOS 사전 점검 실패 항목을 정리한다"
        : "다음 C4 실행계약으로 진행한다";

  return (
    <div className="flex min-w-0 max-w-full flex-col gap-4">
      <PluginSlot name="agent-os:top" />

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-text-secondary" />
              <CardTitle>Agent OS</CardTitle>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StateBadge
                ok={status.detected}
                label={status.detected ? "LEOS detected" : "LEOS missing"}
              />
              <Button
                ghost
                size="icon"
                onClick={load}
                aria-label="Refresh Agent OS status"
              >
                {loading ? <Spinner /> : <RefreshCw />}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-8">
          <div className="min-w-0">
            <p className="text-xs text-text-tertiary">Root</p>
            <p className="mt-1 break-words font-mono-ui text-xs text-text-secondary">
              {status.root}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Contracts</p>
            <p className="mt-1 text-sm text-text-primary">
              {status.contracts.length} visible
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Adapter skills</p>
            <p className="mt-1 text-sm text-text-primary">
              {installedSkills}/{status.skills.length} installed
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Governor bridge</p>
            <p className="mt-1 text-sm text-text-primary">
              {status.bridge.active
                ? "declared"
                : status.bridge.installed
                  ? "incomplete"
                  : "missing"}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Citizens</p>
            <p className="mt-1 text-sm text-text-primary">
              {harnessedCitizens}/{status.citizens.length} manifested
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Preflight</p>
            <p className="mt-1 text-sm text-text-primary">
              {status.preflight.status}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Work frame</p>
            <p className="mt-1 text-sm text-text-primary">
              {status.work_frame.status}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Contract gate</p>
            <p className="mt-1 text-sm text-text-primary">
              {status.contract_gate.status}
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-text-secondary" />
              <CardTitle>PM Observation</CardTitle>
            </div>
            <Badge tone={pmObservationTone} className="normal-case">
              {pmObservationLabel}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="min-w-0">
            <p className="text-xs text-text-tertiary">작업 프레임</p>
            <p className="mt-1 text-sm text-text-primary">
              {workFrameApplied
                ? `적용됨 (${workFrameReadyFields}/${status.work_frame.required_fields.length})`
                : `확인 필요 (${workFrameReadyFields}/${status.work_frame.required_fields.length})`}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-xs text-text-tertiary">계약 종료 차단</p>
            <p className="mt-1 text-sm text-text-primary">
              {contractGateVisibleOnly ? "꺼짐: 관측만 가능" : "켜짐: 차단 후보"}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-xs text-text-tertiary">LEOS 사전 점검</p>
            <p className="mt-1 text-sm text-text-primary">
              {preflightFailing ? "실패 항목 있음" : status.preflight.status}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-xs text-text-tertiary">다음 행동</p>
            <p className="mt-1 break-words text-sm text-text-primary">
              {nextGovernedAct}
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-text-secondary" />
              <CardTitle>LEOS Preflight</CardTitle>
            </div>
            <Badge
              tone={preflightTone(status.preflight.status)}
              className="normal-case"
            >
              {status.preflight.status}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 xl:grid-cols-2">
          {preflightChecks.length === 0 ? (
            <p className="text-sm text-text-secondary">
              No LEOS preflight checks are available.
            </p>
          ) : (
            preflightChecks.map(([name, check]) => (
              <div key={name} className="rounded border border-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono-ui text-xs text-text-primary">
                    {name}
                  </span>
                  <Badge
                    tone={
                      check.ok === true
                        ? "success"
                        : check.ok === false
                          ? "destructive"
                          : "warning"
                    }
                    className="normal-case"
                  >
                    {check.ok === true
                      ? "pass"
                      : check.ok === false
                        ? "fail"
                        : "unavailable"}
                  </Badge>
                </div>
                {check.summary && (
                  <p className="mt-2 break-words font-mono-ui text-xs text-text-secondary">
                    {check.summary}
                  </p>
                )}
                {check.failed_checks && check.failed_checks.length > 0 && (
                  <p className="mt-2 break-words text-xs text-warning">
                    {check.failed_checks.join(", ")}
                  </p>
                )}
                {check.violations && check.violations.length > 0 && (
                  <p className="mt-2 break-words text-xs text-warning">
                    {check.violations.join(", ")}
                  </p>
                )}
              </div>
            ))
          )}
          <div className="rounded border border-border p-3">
            <p className="text-xs text-text-tertiary">Signed head coverage</p>
            <p className="mt-1 font-mono-ui text-xs text-text-secondary">
              {signedHead.sealed_entries ?? "unknown"} /{" "}
              {signedHead.ledger_entries ?? "unknown"}
            </p>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <LoopAxisCard
          title="Law"
          icon={Scale}
          status={status.loop.law.status}
          metrics={[
            { label: "Refs", value: String(status.loop.law.refs.length) },
            {
              label: "In-force PRD",
              value: String(status.loop.law.in_force_prd_count),
            },
          ]}
          warnings={status.loop.law.warnings}
        />
        <LoopAxisCard
          title="Contracts"
          icon={GitBranch}
          status={status.loop.contracts.status}
          metrics={[
            {
              label: "Active",
              value: String(status.loop.contracts.active_count),
            },
            {
              label: "Cascade",
              value: formatCascadeTiers(status.loop.contracts.cascade_tiers),
            },
            {
              label: "Coexistence",
              value: status.loop.contracts.coexistence_active ? "active" : "missing",
            },
            {
              label: "Knowledge",
              value: status.loop.contracts.knowledge_active ? "active" : "missing",
            },
          ]}
          warnings={status.loop.contracts.warnings}
        />
        <LoopAxisCard
          title="Knowledge"
          icon={Database}
          status={status.loop.knowledge.status}
          metrics={[
            {
              label: "Refs",
              value: String(status.loop.knowledge.refs.length),
            },
            {
              label: "Evergreen",
              value: String(knowledgeCounts.evergreen ?? 0),
            },
            {
              label: "Fleeting",
              value: String(knowledgeCounts.fleeting ?? 0),
            },
            {
              label: "Sources",
              value: String(knowledgeCounts.sources ?? 0),
            },
          ]}
          warnings={status.loop.knowledge.warnings}
        />
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <FileCheck2 className="h-4 w-4 text-text-secondary" />
              <CardTitle>Work Frame</CardTitle>
            </div>
            <Badge
              tone={workFrameTone(status.work_frame.status)}
              className="normal-case"
            >
              {status.work_frame.status}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 xl:grid-cols-3">
          <div className="min-w-0">
            <p className="text-xs text-text-tertiary">Active contract</p>
            <p className="mt-1 break-words font-mono-ui text-xs text-text-primary">
              {status.work_frame.active_contract_ref ?? "not bound"}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-xs text-text-tertiary">Injected by</p>
            <p className="mt-1 break-words font-mono-ui text-xs text-text-primary">
              {status.work_frame.injected_by ?? "not detected"}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Required fields</p>
            <p className="mt-1 text-sm text-text-primary">
              {workFrameReadyFields}/{status.work_frame.required_fields.length} detected
            </p>
          </div>
          <div className="xl:col-span-3">
            <div className="flex flex-wrap gap-2">
              {status.work_frame.required_fields.map((field) => (
                <Badge
                  key={field}
                  tone={
                    status.work_frame.field_markers[field]
                      ? "success"
                      : "warning"
                  }
                  className="normal-case"
                >
                  {field}
                </Badge>
              ))}
            </div>
            {status.work_frame.warnings.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {status.work_frame.warnings.map((warning) => (
                  <Badge key={warning} tone="warning" className="normal-case">
                    {warning}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-text-secondary" />
              <CardTitle>Contract Gate</CardTitle>
            </div>
            <Badge
              tone={contractGateTone(status.contract_gate.status)}
              className="normal-case"
            >
              {status.contract_gate.status}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 xl:grid-cols-4">
          <div>
            <p className="text-xs text-text-tertiary">Mode</p>
            <p className="mt-1 break-words font-mono-ui text-xs text-text-primary">
              {status.contract_gate.mode}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Enabled</p>
            <p className="mt-1 text-sm text-text-primary">
              {status.contract_gate.enabled ? "true" : "false"}
            </p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Fail-open</p>
            <p className="mt-1 text-sm text-text-primary">
              {status.contract_gate.fail_open ? "true" : "false"}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-xs text-text-tertiary">Source</p>
            <p className="mt-1 break-words font-mono-ui text-xs text-text-primary">
              {status.contract_gate.source}
            </p>
          </div>
          {status.contract_gate.warnings.length > 0 && (
            <div className="xl:col-span-4">
              <div className="flex flex-wrap gap-2">
                {status.contract_gate.warnings.map((warning) => (
                  <Badge key={warning} tone="warning" className="normal-case">
                    {warning}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {status.warnings.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-warning" />
              <CardTitle>Warnings</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {status.warnings.map((warning) => (
              <Badge key={warning} tone="warning" className="normal-case">
                {warning}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-text-secondary" />
              <CardTitle>Constitution</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <dl>
              <DetailRow label="Path" value={status.constitution.path} />
              <DetailRow
                label="Exists"
                value={status.constitution.exists ? "yes" : "no"}
              />
              <DetailRow
                label="SHA-256"
                value={shortHash(status.constitution.sha256)}
              />
              <DetailRow
                label="Modified"
                value={status.constitution.mtime ?? "not available"}
              />
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-text-secondary" />
              <CardTitle>Runtime</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <dl>
              <DetailRow label="API mode" value={status.runtime.api_mode} />
              <DetailRow label="Memory" value={status.runtime.memory_provider} />
              <DetailRow
                label="Tool progress"
                value={status.runtime.tool_progress_bridge}
              />
              <DetailRow
                label="Checked"
                value={status.validation.last_checked_at}
              />
            </dl>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-text-secondary" />
              <CardTitle>Governor Bridge</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Badge
                tone={
                  status.bridge.active
                    ? "success"
                    : status.bridge.installed
                      ? "warning"
                      : "destructive"
                }
                className="normal-case"
              >
                {status.bridge.active
                  ? "declared"
                  : status.bridge.installed
                    ? "incomplete"
                    : "missing"}
              </Badge>
              <Badge tone="secondary" className="normal-case">
                {readyHooks}/{status.bridge.required_hooks.length} hooks ready
              </Badge>
            </div>
            <dl>
              <DetailRow label="Plugin" value={status.bridge.plugin_path} />
              <DetailRow label="Version" value={status.bridge.version} />
            </dl>
            <div className="mt-3 grid gap-2">
              {status.bridge.required_hooks.map((hook) => {
                const declared = status.bridge.hooks_declared.includes(hook);
                const fileReady = status.bridge.hook_files[hook];
                const ok = declared && fileReady;
                return (
                  <div
                    key={hook}
                    className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 py-2 last:border-b-0"
                  >
                    <span className="font-mono-ui text-xs text-text-primary">
                      {hook}
                    </span>
                    <div className="flex items-center gap-2">
                      {ok ? (
                        <CheckCircle2 className="h-4 w-4 text-success" />
                      ) : (
                        <XCircle className="h-4 w-4 text-destructive" />
                      )}
                      <Badge
                        tone={ok ? "success" : "destructive"}
                        className="normal-case"
                      >
                        {ok ? "declared" : declared ? "file missing" : "undeclared"}
                      </Badge>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Users className="h-4 w-4 text-text-secondary" />
              <CardTitle>Citizens</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="grid gap-2">
            {status.citizens.length === 0 ? (
              <p className="text-sm text-text-secondary">
                No LEOS citizen manifests are visible.
              </p>
            ) : (
              <div className="grid gap-2 md:grid-cols-2">
                {status.citizens.map((citizen) => (
                  <div
                    key={`${citizen.source}:${citizen.id}`}
                    className="min-w-0 rounded border border-border p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-mono-ui text-xs text-text-primary">
                        {citizen.id}
                      </span>
                      <Badge
                        tone={citizen.harnessed ? "success" : "warning"}
                        className="normal-case"
                      >
                        {citizen.harnessed ? "manifested" : "incomplete"}
                      </Badge>
                    </div>
                    <p className="mt-2 break-words text-xs text-text-secondary">
                      {citizen.class || "unclassified"} /{" "}
                      {citizen.citizenship || "unknown"}
                    </p>
                    <p className="mt-1 font-mono-ui text-xs text-text-tertiary">
                      {citizen.authority || "no authority"} /{" "}
                      {citizen.action_trust || "no trust"}
                    </p>
                    {citizen.role_title && (
                      <p className="mt-1 truncate text-xs text-text-tertiary">
                        {citizen.role_title}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FileCheck2 className="h-4 w-4 text-text-secondary" />
            <CardTitle>Contracts</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid gap-2">
          {status.contracts.length === 0 ? (
            <p className="text-sm text-text-secondary">
              No LEOS contracts are visible.
            </p>
          ) : (
            status.contracts.map((contract) => (
              <div key={contract.id} className="rounded border border-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono-ui text-xs text-text-primary">
                    {contract.id}
                  </span>
                  <Badge tone="secondary" className="normal-case">
                    {contract.status}
                  </Badge>
                </div>
                <p className="mt-2 break-words font-mono-ui text-xs text-text-secondary">
                  {contract.path}
                </p>
                <p className="mt-1 font-mono-ui text-xs text-text-tertiary">
                  sha256 {shortHash(contract.sha256)}
                </p>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-text-secondary" />
              <CardTitle>Adapter Skills</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="grid gap-2">
            {status.skills.map((skill) => {
              const ok = skill.installed && skill.enabled;
              return (
                <div
                  key={skill.name}
                  className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 py-2 last:border-b-0"
                >
                  <span className="font-mono-ui text-xs text-text-primary">
                    {skill.name}
                  </span>
                  <div className="flex items-center gap-2">
                    {ok ? (
                      <CheckCircle2 className="h-4 w-4 text-success" />
                    ) : (
                      <XCircle className="h-4 w-4 text-destructive" />
                    )}
                    <Badge
                      tone={ok ? "success" : "destructive"}
                      className="normal-case"
                    >
                      {skill.installed
                        ? skill.enabled
                          ? "enabled"
                          : "disabled"
                        : "missing"}
                    </Badge>
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Validation Evidence</CardTitle>
          </CardHeader>
          <CardContent>
            {status.validation.evidence.length === 0 ? (
              <p className="text-sm text-text-secondary">
                No validation evidence has been collected yet.
              </p>
            ) : (
              <ul className="grid gap-2">
                {status.validation.evidence.map((item) => (
                  <li
                    key={item}
                    className="flex items-center gap-2 text-sm text-text-secondary"
                  >
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <PluginSlot name="agent-os:bottom" />
    </div>
  );
}
