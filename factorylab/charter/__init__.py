"""What the population says it is for, and what departing from it costs.

Owns immutable charter editions, their metric cards, the executable
measurements those cards name, the price controller that raises a card's λ
while the factory sits outside its acceptable region, amendments, the committee
that votes on them and the book that records every edition in order.

Imports ``kernel``. ``measurement`` also reaches up into
``runtime.observations`` and ``runtime.cards``, lazily and inside functions, for
the observation vocabulary and region parsing the runtime owns; the charter
itself never runs the loop.
"""
