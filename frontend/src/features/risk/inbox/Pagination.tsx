import { Button } from '@/components/ui';

interface Props {
  page: number;
  pageSize: number;
  count: number;
  onPage: (p: number) => void;
}

export function Pagination({ page, pageSize, count, onPage }: Props) {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  const from = count === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(count, page * pageSize);
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-2 text-sm text-muted">
      <span>
        {from}-{to} of {count}
      </span>
      <div className="flex items-center gap-2">
        <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <span>
          Page {page} of {pages}
        </span>
        <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </nav>
  );
}
