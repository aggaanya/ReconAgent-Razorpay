function formatMinor(amount, currency = 'INR') {
  if (amount === null || amount === undefined) return '—'
  const formatter = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  })
  return formatter.format(amount / 100)
}

function formatDifference(minor, currency) {
  if (minor === null || minor === undefined) return '—'
  return `${minor > 0 ? '+' : ''}${formatMinor(minor, currency)}`
}

function formatCompactMinor(amount, currency = 'INR') {
  if (amount === null || amount === undefined) return '—'
  const rupees = amount / 100
  if (rupees >= 100000) {
    return `₹${(rupees / 100000).toFixed(2)}L`
  }
  if (rupees >= 1000) {
    return `₹${(rupees / 1000).toFixed(1)}K`
  }
  return formatMinor(amount, currency)
}

export { formatDifference, formatMinor, formatCompactMinor }
