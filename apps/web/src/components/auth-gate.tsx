"use client";

import { useEffect, type ReactNode } from "react";
import { useIsAuthenticated } from "@refinedev/core";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

const PUBLIC_ROUTES = new Set(["/login", "/callback"]);

export function AuthGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const isPublicRoute = PUBLIC_ROUTES.has(pathname);
  const auth = useIsAuthenticated({
    queryOptions: { enabled: !isPublicRoute, staleTime: 0, refetchOnMount: "always" },
  });
  const checking = auth.isPending || (auth.isFetching && !auth.data?.authenticated);

  useEffect(() => {
    if (isPublicRoute || checking || auth.data?.authenticated) return;
    const query = searchParams.toString();
    const destination = `${pathname}${query ? `?${query}` : ""}`;
    router.replace(`/login?to=${encodeURIComponent(destination)}`);
  }, [auth.data?.authenticated, checking, isPublicRoute, pathname, router, searchParams]);

  if (isPublicRoute) return children;
  if (checking || !auth.data?.authenticated) {
    return (
      <div className="auth-screen" role="status" aria-live="polite">
        <div className="auth-loading">
          <span className="auth-brand-mark" aria-hidden="true">P</span>
          <span>Έλεγχος πρόσβασης…</span>
        </div>
      </div>
    );
  }
  return children;
}
