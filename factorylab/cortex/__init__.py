"""One assembly: a model, a prompt, a contract, a budget, and the jail it works inside.

Owns the assembly primitive and the validation every return passes, the request
and return shapes, the schematics of the world an assembly reads on each call,
the tool contracts it may buy, and the sandbox population code runs in. An
assembly is not a process: it exists only while answering one event.

Imports ``kernel``, ``charter``, ``settlement`` and ``world``, and reaches up
into ``runtime`` for the observation and action vocabularies the loop owns.
Nothing here owns money, scores or the diary.
"""
