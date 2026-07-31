"use client";

import { useCustom } from "@refinedev/core";
import { Layers3 } from "lucide-react";

import { activeKeywords, type BusinessScope } from "@/lib/business-scope";
import type { SectorProfileResponse } from "@/lib/api";

export function SectorProfilePicker({
  profile,
  onApply,
}: {
  profile: BusinessScope;
  onApply: (profile: BusinessScope) => void;
}) {
  const query = useCustom<SectorProfileResponse[]>({ url: "/v1/sector-profiles", method: "get", queryOptions: { retry: 1 } });
  const templates = query.query.isSuccess ? query.result.data : [];
  if (!templates.length) return null;
  return (
    <section className="sector-profile-picker" aria-labelledby="sector-profiles-title">
      <div><Layers3 size={17} /><span><strong id="sector-profiles-title">Έτοιμα sector profiles</strong><small>Προσθέτουν CPV και λεκτικά στο draft. Η εφαρμογή παραμένει ρητή.</small></span></div>
      <div>
        {templates.map((template) => (
          <button type="button" key={template.code} title={template.description} onClick={() => {
            const cpvPrefixes = Array.from(new Set([...profile.cpvPrefixes, ...template.cpv_prefixes]));
            const keywords = Array.from(new Set([...activeKeywords(profile), ...template.keywords]));
            onApply({
              ...profile,
              cpvPrefix: cpvPrefixes[0] ?? "",
              cpvPrefixes,
              keyword: keywords.join(", "),
              keywords,
              excludedKeywords: Array.from(new Set([...profile.excludedKeywords, ...template.excluded_keywords])),
            });
          }}>
            <strong>{template.name}</strong>
            <small>{template.cpv_prefixes.join(" · ")}</small>
          </button>
        ))}
      </div>
    </section>
  );
}
