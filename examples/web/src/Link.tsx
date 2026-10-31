import type { ReactNode } from "react";

import { navigate, usePath } from "./route.ts";

interface LinkProps {
  to: string;
  children: ReactNode;
}

/** A link to a page of the application, marked as current while it is shown. */
export function Link({ to, children }: LinkProps) {
  const path = usePath();
  return (
    <a
      href={to}
      aria-current={to === path ? "page" : undefined}
      onClick={(event) => {
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}
