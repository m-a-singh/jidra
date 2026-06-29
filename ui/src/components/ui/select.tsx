import * as React from "react";
import { cn } from "../../lib/utils";

const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "bg-bg border border-border-mid rounded-md text-text text-sm px-2 py-1.5 outline-none cursor-pointer transition-colors focus:border-accent-dim",
        className
      )}
      {...props}
    />
  )
);
Select.displayName = "Select";

export { Select };
