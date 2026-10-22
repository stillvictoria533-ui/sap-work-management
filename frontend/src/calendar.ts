/** Build Monday-first calendar dates in UTC so browser timezone never shifts a cell. */
export function calendarDays(
  month: string,
): { date: string; day: number; currentMonth: boolean }[] {
  if (!/^[1-9]\d{3}-(0[1-9]|1[0-2])$/.test(month)) return [];
  const [year, number] = month.split("-").map(Number);
  const first = new Date(Date.UTC(year!, number! - 1, 1));
  const length = new Date(Date.UTC(year!, number!, 0)).getUTCDate();
  const offset = (first.getUTCDay() + 6) % 7;
  const cells = Math.ceil((offset + length) / 7) * 7;
  return Array.from({ length: cells }, (_, i) => {
    const date = new Date(Date.UTC(year!, number! - 1, i - offset + 1));
    return {
      date: date.toISOString().slice(0, 10),
      day: date.getUTCDate(),
      currentMonth: date.getUTCMonth() === number! - 1,
    };
  });
}

export function shiftMonth(month: string, offset: number): string {
  const [year, number] = month.split("-").map(Number);
  return new Date(Date.UTC(year!, number! - 1 + offset, 1))
    .toISOString()
    .slice(0, 7);
}
