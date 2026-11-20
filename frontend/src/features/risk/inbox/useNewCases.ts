import { useEffect, useRef, useState } from 'react';

export const HIGHLIGHT_MS = 5000;

/**
 * Detects ids that show up in later polls of the same view (same `resetKey`).
 * The first batch for a view only seeds the "seen" set. Pass `ids` undefined while
 * loading or while showing placeholder data.
 */
export function useNewCases(ids: string[] | undefined, resetKey: string) {
  const seen = useRef<Set<string> | null>(null);
  const keyRef = useRef(resetKey);
  const timers = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [announcement, setAnnouncement] = useState('');
  const idsKey = ids?.join('|');

  useEffect(() => {
    if (keyRef.current !== resetKey) {
      keyRef.current = resetKey;
      seen.current = null;
      setFresh(new Set());
      setAnnouncement('');
    }
    if (!ids) return;
    if (seen.current === null) {
      seen.current = new Set(ids);
      return;
    }
    const known = seen.current;
    const added = ids.filter((id) => !known.has(id));
    if (added.length === 0) return;
    added.forEach((id) => known.add(id));
    setFresh((prev) => new Set([...prev, ...added]));
    setAnnouncement(`${added.length} new ${added.length === 1 ? 'case' : 'cases'}: ${added.join(', ')}`);
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setFresh((prev) => {
        const next = new Set(prev);
        added.forEach((id) => next.delete(id));
        return next;
      });
    }, HIGHLIGHT_MS);
    timers.current.add(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- idsKey stands in for ids
  }, [idsKey, resetKey]);

  useEffect(() => {
    const set = timers.current;
    return () => set.forEach(clearTimeout);
  }, []);

  return { fresh, announcement };
}
