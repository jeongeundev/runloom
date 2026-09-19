export function render(order) {
  return {
    shipping: order.shippingFee === null ? 'pending' : String(order.shippingFee),
    total: order.total === null ? 'pending' : String(order.total),
  };
}
