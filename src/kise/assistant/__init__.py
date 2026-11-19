"""The Assistant context: turning speech, text and receipt photos into draft expenses.

It never writes an expense. It reads what the person said, typed or photographed, asks a language
model to pull out the spending in it, and checks every figure the model returned against the
Owner's real categories and units and against the calendar — then hands back *drafts* for the
person to confirm. Recording a confirmed draft goes through the ordinary expenses API, with all its
rules, exactly as if it had been typed in.

It also answers questions about spending ("how much on food this month?") from facts the server
computed, so the model reports numbers rather than inventing them.

Layout, as everywhere else:

``domain/``          drafts and the rules that make a model's output trustworthy (pure Python)
``application/``     the use cases and the ports they need: a language model, the Owner's spending
``infrastructure/``  the Gemini adapter, the disabled adapter, the rate limiter
``presentation/``    the HTTP router
"""
