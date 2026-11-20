import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import clsx from 'clsx';
import type { AffectedEntityType, EvidenceRef, RiskAnalysis, RiskCaseState } from '../../../api/types';
import { Badge, EvidenceTag, SeverityBadge, Spinner } from '../../../components/ui';

export interface AnalysisPanelProps {
  analysis: RiskAnalysis | null;
  status?: RiskCaseState;
}

const ENTITY_LABEL: Record<AffectedEntityType, string> = {
  PO: 'Purchase orders',
  SUPPLIER: 'Suppliers',
  ITEM: 'Items',
  SHIPMENT: 'Shipments',
  LOCATION: 'Locations',
};
const ENTITY_ORDER: AffectedEntityType[] = ['PO', 'SUPPLIER', 'ITEM', 'SHIPMENT', 'LOCATION'];

const chipCls =
  'inline-flex items-center rounded border border-border bg-surface-2 px-2 py-0.5 font-mono text-xs text-fg';

function EntityRef({ id, link }: { id: string; link: boolean }) {
  return link ? (
    <Link to={`/suppliers/${encodeURIComponent(id)}`} className="font-mono text-primary underline underline-offset-2">
      {id}
    </Link>
  ) : (
    <span className="font-mono">{id}</span>
  );
}

function ScoreMeter({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score));
  return (
    <div className="min-w-40">
      <div className="flex items-baseline justify-between text-xs text-muted">
        <span id="risk-score-label">Risk score</span>
        <span className="font-mono text-sm font-semibold text-fg">{score}/100</span>
      </div>
      <div
        role="meter"
        aria-labelledby="risk-score-label"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={`${pct} out of 100`}
        className="mt-1 h-2 w-full overflow-hidden rounded-full bg-surface-2"
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Group({
  title,
  count,
  children,
  className,
}: {
  title: string;
  count: number;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section aria-label={title} className={clsx('rounded-lg border border-border p-3', className)}>
      <h3 className="mb-2 text-sm font-semibold text-fg">
        {title} <span className="font-normal text-muted">({count})</span>
      </h3>
      {children}
    </section>
  );
}

function EvidenceRow({ e }: { e: EvidenceRef }) {
  const missing = e.value === null;
  return (
    <tr className="border-t border-border align-top">
      <td className="px-2 py-1.5">
        <Badge>{e.source}</Badge>
      </td>
      <td className="px-2 py-1.5 font-mono text-xs">
        <EntityRef id={e.entity} link={e.entity.startsWith('SUP-')} />
        {(e.item_id || e.location_id) && (
          <div className="text-muted">
            {e.item_id && <span>item {e.item_id} </span>}
            {e.location_id && <span>at {e.location_id}</span>}
          </div>
        )}
      </td>
      <td className="px-2 py-1.5 font-mono text-xs">{e.field}</td>
      <td className="px-2 py-1.5 font-mono text-xs">
        {missing ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="italic text-danger">missing</span>
            <EvidenceTag kind="MISSING_DATA" />
          </span>
        ) : (
          String(e.value)
        )}
      </td>
    </tr>
  );
}

export function AnalysisPanel({ analysis, status }: AnalysisPanelProps) {
  if (!analysis) {
    if (status === 'RECEIVED' || status === 'ANALYZING') {
      return (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner size="sm" label="Analysis in progress" />
          <span>AI analysis in progress…</span>
        </div>
      );
    }
    return <p className="text-sm text-muted">No analysis available</p>;
  }

  const a = analysis;
  const none = <p className="text-sm text-muted">None reported</p>;
  const byType = ENTITY_ORDER.map((t) => ({ t, items: a.affected_entities.filter((x) => x.type === t) })).filter(
    (g) => g.items.length > 0,
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3 rounded-lg border border-border bg-surface p-3">
        <div className="flex flex-wrap items-center gap-4">
          <SeverityBadge severity={a.risk_level} />
          <ScoreMeter score={a.risk_score} />
          <Badge tone={a.confidence === 'LOW' ? 'warning' : 'neutral'}>
            {a.confidence === 'LOW' && <span aria-hidden="true">⚠</span>}
            Confidence: {a.confidence}
          </Badge>
        </div>
        <p className="text-sm text-fg">{a.summary}</p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Group title="Facts" count={a.facts.length}>
          {a.facts.length === 0 ? (
            none
          ) : (
            <ul className="space-y-2">
              {a.facts.map((f, i) => (
                <li key={i} className="flex flex-wrap items-start gap-2 text-sm">
                  <EvidenceTag kind="FACT" />
                  <span className="min-w-0 flex-1">{f}</span>
                </li>
              ))}
            </ul>
          )}
        </Group>

        <Group title="Estimates" count={a.estimates.length} className="border-dashed">
          {a.estimates.length === 0 ? (
            none
          ) : (
            <ul className="space-y-2">
              {a.estimates.map((e, i) => (
                <li key={i} className="flex flex-wrap items-start gap-2 text-sm italic">
                  <EvidenceTag kind="ESTIMATE" />
                  <span className="min-w-0 flex-1">
                    {e.statement}
                    <span className="block text-xs text-muted">Basis: {e.basis}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Group>

        <Group title="Assumptions" count={a.assumptions.length} className="border-dotted">
          {a.assumptions.length === 0 ? (
            none
          ) : (
            <ul className="space-y-2">
              {a.assumptions.map((s, i) => (
                <li key={i} className="flex flex-wrap items-start gap-2 text-sm">
                  <EvidenceTag kind="ASSUMPTION" />
                  <span className="min-w-0 flex-1">{s}</span>
                </li>
              ))}
            </ul>
          )}
        </Group>

        <Group title="Missing data" count={a.missing_data.length} className="border-2 border-warning/60 bg-warning/10">
          {a.missing_data.length === 0 ? (
            <p className="text-sm text-muted">No data gaps reported</p>
          ) : (
            <div>
              <p className="mb-2 text-sm font-semibold text-warning">The AI did not have:</p>
              <ul className="space-y-2">
                {a.missing_data.map((m, i) => (
                  <li key={i} className="flex flex-wrap items-start gap-2 text-sm">
                    <EvidenceTag kind="MISSING_DATA" />
                    <span className="min-w-0 flex-1">{m}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Group>
      </div>

      <section aria-label="Affected entities">
        <h3 className="mb-2 text-sm font-semibold text-fg">
          Affected entities <span className="font-normal text-muted">({a.affected_entities.length})</span>
        </h3>
        {byType.length === 0 ? (
          none
        ) : (
          <div className="space-y-2">
            {byType.map(({ t, items }) => (
              <div key={t} className="flex flex-wrap items-center gap-2">
                <span className="w-32 text-xs text-muted">{ENTITY_LABEL[t]}</span>
                <ul className="flex flex-wrap gap-1.5">
                  {items.map((x) => (
                    <li key={x.id} className={chipCls}>
                      <EntityRef id={x.id} link={t === 'SUPPLIER'} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </section>

      <section aria-label="Evidence">
        <h3 className="mb-2 text-sm font-semibold text-fg">
          Evidence <span className="font-normal text-muted">({a.evidence.length})</span>
        </h3>
        {a.evidence.length === 0 ? (
          none
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left text-sm">
              <thead className="bg-surface-2 text-xs text-muted">
                <tr>
                  <th scope="col" className="px-2 py-1.5 font-medium">Source</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Entity</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Field</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Value</th>
                </tr>
              </thead>
              <tbody>
                {a.evidence.map((e, i) => (
                  <EvidenceRow key={i} e={e} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
