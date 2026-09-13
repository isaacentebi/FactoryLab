"""Factory Lab: one bounded world, one wallet, and a population that pays to think.

The packages, in dependency order. Each may import the ones above it and never
the ones below.

``kernel``      conserved money, sealed evidence, event delivery, contracts,
                the decision queue, the novelty reserve, timing, termination.
``learners``    no-regret algorithms over an action set. Pure; imports nothing.
``charter``     the norms the population judges itself against, their metric
                cards, their measurement and their prices.
``settlement``  sealed forecasts, consequence scoring and standing.
``cortex``      an assembly: a model, a prompt, a contract and a budget; the
                jail its tools run in; the world block it reads.
``world``       adapters to everything outside: venue, model rails, x402,
                treasury rails, metering, the scripted stand-ins.
``runtime``     the event loop that puts them together, and the CLI.
``versioning``  read-only analysis of a dead world's diary.
"""
