export interface BusinessScope {
  keyword: string;
  keywords: string[];
  cpvPrefix: string;
  cpvPrefixes: string[];
  nutsCode: string;
  municipality: string;
  amountMin: string;
  dateFrom: string;
  dateTo: string;
  companyAfm: string;
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

export function activeCpvPrefixes(scope: BusinessScope): string[] {
  return unique(scope.cpvPrefixes.length ? scope.cpvPrefixes : [scope.cpvPrefix]);
}

export function activeKeywords(scope: BusinessScope): string[] {
  return unique(scope.keywords.length ? scope.keywords : [scope.keyword]);
}

export function businessScopeQuery(scope: BusinessScope): Record<string, string | number> {
  const cpvPrefixes = activeCpvPrefixes(scope);
  const keywords = activeKeywords(scope);
  return {
    ...(cpvPrefixes.length ? { cpv_prefixes: cpvPrefixes.join(",") } : {}),
    ...(keywords.length ? { keywords: keywords.join(",") } : {}),
    ...(keywords.length ? { taxonomy_match: "KEYWORD_REQUIRED" } : {}),
    ...(scope.nutsCode.trim() ? { nuts_code: scope.nutsCode.trim().toUpperCase() } : {}),
    ...(scope.municipality.trim() ? { municipality: scope.municipality.trim() } : {}),
    ...(Number(scope.amountMin) > 0 ? { amount_min: Number(scope.amountMin) } : {}),
    ...(scope.dateFrom ? { date_from: scope.dateFrom } : {}),
    ...(scope.dateTo ? { date_to: scope.dateTo } : {}),
    ...(/^\d{9}$/.test(scope.companyAfm.trim()) ? { reference_afm: scope.companyAfm.trim() } : {}),
  };
}

export function businessScopeFingerprint(scope: BusinessScope): string {
  return JSON.stringify({
    cpv: activeCpvPrefixes(scope),
    keywords: activeKeywords(scope),
    nutsCode: scope.nutsCode,
    municipality: scope.municipality,
    amountMin: scope.amountMin,
    dateFrom: scope.dateFrom,
    dateTo: scope.dateTo,
    companyAfm: scope.companyAfm,
  });
}
