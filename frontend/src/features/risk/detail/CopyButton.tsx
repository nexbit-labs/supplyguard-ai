import { useState } from 'react';
import { Button } from '@/components/ui';

export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const onClick = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <Button variant="ghost" size="sm" aria-label={label} onClick={() => void onClick()} className="h-6 px-2 text-xs">
      {copied ? 'Copied' : 'Copy'}
    </Button>
  );
}
