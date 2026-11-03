// Format API Decimal strings without floating-point conversion or price calculations.
export function formatDecimal(value: string, trimFraction = true): string {
  const [whole, fraction = ''] = value.split('.');
  const decimals = trimFraction ? fraction.replace(/0+$/, '') : fraction;
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0') + (decimals ? `,${decimals}` : '');
}
