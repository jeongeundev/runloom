export function summarize(subtotal, shippingFee) {
  return {subtotal, shippingFee, total: shippingFee === null ? null : subtotal + shippingFee};
}
