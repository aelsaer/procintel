export function formatAmount(value: number | null | undefined, currency = "EUR"): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("el-GR", { style: "currency", currency }).format(value);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("el-GR", { dateStyle: "medium" }).format(new Date(value));
}
