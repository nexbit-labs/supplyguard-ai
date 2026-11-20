import { useEffect, useRef, useState } from 'react';
import { useSimulateDisruption } from '@/api/queries';
import { ApiError } from '@/api/client';
import { Button } from '@/components/ui';
import { SCENARIOS } from '@/mocks/scenarios';

/** Admin-only trigger: pick a scenario, POST it, show pending / error / event id. */
export function SimulateDisruption() {
  const [open, setOpen] = useState(false);
  const [scenario, setScenario] = useState(SCENARIOS[0].name);
  const [note, setNote] = useState<string | null>(null);
  const mutation = useSimulateDisruption();
  const firstRadio = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) firstRadio.current?.focus();
  }, [open]);

  const close = () => {
    setOpen(false);
    mutation.reset();
  };

  const run = () => {
    setNote(null);
    mutation.mutate(scenario, {
      onSuccess: (res) => {
        setOpen(false);
        setNote(`Disruption event ${res.event_id} accepted. The new case appears below in a moment.`);
      },
    });
  };

  const error =
    mutation.error instanceof ApiError
      ? mutation.error.message
      : mutation.error
        ? 'Could not start the simulation. Try again.'
        : null;

  return (
    <div className="relative">
      <Button
        onClick={() => {
          setNote(null);
          mutation.reset();
          setOpen((o) => !o);
        }}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        Simulate Supplier Disruption
      </Button>
      {open && (
        <div
          role="dialog"
          aria-label="Simulate supplier disruption"
          onKeyDown={(e) => {
            if (e.key === 'Escape') close();
          }}
          className="absolute right-0 z-20 mt-2 w-80 space-y-3 rounded-lg border border-border bg-surface p-4 shadow-lg"
        >
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-text">Choose a scenario</legend>
            {SCENARIOS.map((s, i) => (
              <label key={s.name} className="flex cursor-pointer items-start gap-2 text-sm">
                <input
                  ref={i === 0 ? firstRadio : undefined}
                  type="radio"
                  name="scenario"
                  value={s.name}
                  checked={scenario === s.name}
                  onChange={() => setScenario(s.name)}
                  className="mt-1"
                />
                <span>
                  <span className="block font-medium text-text">{s.label}</span>
                  <span className="block text-xs text-muted">{s.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {error && (
            <p role="alert" className="text-sm font-medium text-danger">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={close}>
              Cancel
            </Button>
            <Button size="sm" onClick={run} loading={mutation.isPending}>
              {mutation.isPending ? 'Starting...' : 'Run simulation'}
            </Button>
          </div>
        </div>
      )}
      {note && (
        <p role="status" className="mt-2 text-xs text-muted">
          {note}
        </p>
      )}
    </div>
  );
}
