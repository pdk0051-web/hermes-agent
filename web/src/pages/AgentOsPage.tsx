import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  FileCheck2,
  FileText,
  RefreshCw,
  ShieldCheck,
  Sparkles,
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
        <CardContent className="grid gap-4 md:grid-cols-3">
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
