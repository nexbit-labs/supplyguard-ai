import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import { describe, expect, it } from 'vitest';
import { riskCaseDetail45 } from './fixtures';

const dir = resolve(__dirname, '../../../ai/schemas');
const load = (n: string) => JSON.parse(readFileSync(resolve(dir, n), 'utf8'));

function makeAjv() {
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);
  ajv.addSchema(load('common.schema.json'));
  return ajv;
}

describe('RC-000045 fixture vs ai/schemas', () => {
  it('analysis matches risk_analysis.schema.json', () => {
    const validate = makeAjv().compile(load('risk_analysis.schema.json'));
    const ok = validate(riskCaseDetail45.analysis);
    expect(validate.errors ?? []).toEqual([]);
    expect(ok).toBe(true);
  });

  it('recovery plan matches recovery_plan.schema.json', () => {
    const validate = makeAjv().compile(load('recovery_plan.schema.json'));
    const plan = {
      risk_case_id: riskCaseDetail45.id,
      options: riskCaseDetail45.recommendations.map((rec) => {
        const opt: Record<string, unknown> = { ...rec };
        delete opt.id;
        return opt;
      }),
      no_viable_option_reason: riskCaseDetail45.no_viable_option_reason,
    };
    const ok = validate(plan);
    expect(validate.errors ?? []).toEqual([]);
    expect(ok).toBe(true);
  });

  it('includes an option with null estimates and data_gaps', () => {
    const o = riskCaseDetail45.recommendations.find((r) => r.estimated_cost_delta === null);
    expect(o?.estimated_lead_time_days).toBeNull();
    expect(o?.data_gaps.length).toBeGreaterThan(0);
  });
});
