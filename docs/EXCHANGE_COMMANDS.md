# V2 exchange command semantics

Every client mutation is identified by a unique command ID. New orders use
`OrderCommandV2`; cancellations use `CancelOrderCommandV2`, which is a distinct
command rather than an attribute of the original order. The event ledger first
records `command_accepted`, then records either `order_cancelled` or
`cancel_rejected`. Rejections retain the cancel command ID and the original
order ID so a strategy can deterministically reconcile a race without guessing
whether the request was received.

This follows the general FIX model: an order-cancel request has a unique client
identifier, is a separate entity from the original order, and a rejection ties
the request and original order together. See the [FIX Trading Community order
cancel guidance](https://www.fixtrading.org/online-specification/business-area-trade/).
Price-time priority for V2 replace is separately governed by the matching
engine: a same-price size reduction retains priority, while an increase or
price change loses it. This is a declared venue rule, not a claim about every
production venue.
