import { cn } from "../../lib/utils";

function Separator({ className, vertical = false }: { className?: string; vertical?: boolean }) {
  return (
    <div
      className={cn(
        vertical ? "w-px h-3.5 bg-border-mid" : "h-px w-full bg-border-mid",
        className
      )}
    />
  );
}

export { Separator };
