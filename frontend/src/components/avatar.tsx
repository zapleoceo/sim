import { useState } from 'react';
import { cn, initials } from '@/lib/utils';

/** Round avatar: the photo when it loads, otherwise initials. */
export function Avatar({
  name,
  url,
  size = 40,
  className,
}: {
  name: string;
  url?: string | null;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const src = url || null;
  const showImg = src !== null && failed !== src;
  return (
    <span
      className={cn(
        'inline-flex flex-none items-center justify-center overflow-hidden rounded-full bg-accent font-semibold text-accent-foreground select-none',
        className,
      )}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.38) }}
    >
      {showImg ? (
        <img src={src} alt={name} className="size-full object-cover" loading="lazy" onError={() => setFailed(src)} />
      ) : (
        <span aria-hidden="true">{initials(name)}</span>
      )}
    </span>
  );
}
