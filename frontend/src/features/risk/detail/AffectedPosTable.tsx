import type { AffectedPO } from '@/api/types';
import { Card, CardHeader, CardTitle, Money, Table, TBody, TD, TH, THead, TR, TableEmptyRow } from '@/components/ui';
import { formatDate } from './format';

export function AffectedPosTable({ pos }: { pos: AffectedPO[] }) {
  const currency = pos[0]?.value.currency ?? 'INR';
  const total = pos.reduce((a, p) => a + Number(p.value.amount), 0);
  return (
    <Card role="region" aria-labelledby="pos-heading">
      <CardHeader>
        <CardTitle id="pos-heading">Affected purchase orders ({pos.length})</CardTitle>
      </CardHeader>
      <Table aria-labelledby="pos-heading">
        <THead>
          <tr>
            <TH>PO</TH>
            <TH>Item</TH>
            <TH className="text-right">Qty</TH>
            <TH>Due date</TH>
            <TH className="text-right">Value</TH>
            <TH className="text-right">Days late</TH>
            <TH className="text-right">Stock cover</TH>
          </tr>
        </THead>
        <TBody>
          {pos.length === 0 ? (
            <TableEmptyRow colSpan={7}>No affected purchase orders</TableEmptyRow>
          ) : (
            pos.map((p) => (
              <TR key={p.po_id}>
                <TD className="font-mono text-xs">{p.po_id}</TD>
                <TD>
                  {p.item_name}
                  <span className="block font-mono text-xs text-muted">{p.item_id}</span>
                </TD>
                <TD className="text-right tabular-nums">{p.qty}</TD>
                <TD>{formatDate(p.due_date)}</TD>
                <TD className="text-right">
                  <Money value={p.value} />
                </TD>
                <TD className="text-right tabular-nums">{p.days_late_estimate > 0 ? `${p.days_late_estimate} d` : 'On time'}</TD>
                <TD className="text-right tabular-nums">{p.stock_cover_days} d</TD>
              </TR>
            ))
          )}
        </TBody>
        {pos.length > 0 && (
          <tfoot>
            <tr className="border-t border-border bg-surface-2 font-semibold">
              <th scope="row" colSpan={4} className="px-3 py-2 text-left text-sm">
                Total exposure
              </th>
              <td className="px-3 py-2 text-right text-sm" data-testid="total-exposure">
                <Money amount={total.toFixed(2)} currency={currency} />
              </td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        )}
      </Table>
    </Card>
  );
}
