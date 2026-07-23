"use client";

import { Refine } from "@refinedev/core";
import routerProvider from "@refinedev/nextjs-router";
import { Suspense, type ReactNode } from "react";
import { procurementDataProvider } from "@/lib/data-provider";
import { procurementAccessControlProvider, procurementAuthProvider } from "@/lib/auth-provider";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <Suspense>
      <Refine
        routerProvider={routerProvider}
        dataProvider={{ default: procurementDataProvider }}
        authProvider={procurementAuthProvider}
        accessControlProvider={procurementAccessControlProvider}
        resources={[
          { name: "search", list: "/" },
          { name: "contracts", show: "/contracts/:id" },
          { name: "processes", show: "/processes/:id" },
          { name: "buyers", show: "/buyers/:id" },
          { name: "companies", show: "/companies/:id" },
          { name: "business-profile", list: "/" },
          { name: "pipeline", list: "/?view=opportunities" },
          { name: "alert-rules", list: "/?view=alerts" },
          { name: "watches", list: "/?view=competitors" },
          { name: "exports", list: "/?view=analytics" },
        ]}
        options={{
          syncWithLocation: true,
          disableTelemetry: true,
          reactQuery: {
            clientConfig: {
              defaultOptions: {
                queries: {
                  retry: 1,
                  staleTime: 30_000,
                },
              },
            },
          },
        }}
      >
        {children}
      </Refine>
    </Suspense>
  );
}
