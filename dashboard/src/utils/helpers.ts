export const fmt = (iso: string) => new Date(iso).toLocaleString();
export const shortId = (id: string) => id.substring(0, 8) + '…';

export function statusColor(status: number | null): string {
  if (!status) return 'badge-warning';
  if (status >= 200 && status < 300) return 'badge-success';
  if (status >= 400 && status < 500) return 'badge-warning';
  return 'badge-error';
}
