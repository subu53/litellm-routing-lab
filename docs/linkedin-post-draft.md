A 33% latency win that was fake, and the test that proved it.

That number came from a router I built to cut AI inference costs.

The problem: most teams send every request to their strongest model. A one-word
lookup and a hard design question get billed the same. So I built a gateway that
scores each request and routes cheap work to a cheap model.

Straightforward. Ship it.

Then I measured it.

Across 60 labelled prompts, the router sent zero to the strong model. The 33% win
was real, but it was not routing. It was using the small model for everything.

So I did what felt obvious. I tuned the thresholds. Nothing changed. I blamed the
classifier's vocabulary, added keywords, then checked where those words actually came
from: 10 of the 12 appeared in exactly one prompt, and two appeared in none. I had been
training on my own test set.

I also blamed my own threshold change for a while, and wrote that down, wrongly.

The fix was not more tuning. It was a better experiment. Forty fresh prompts in four
cells: short and hard, short and easy, long and hard, long and easy. The pass criteria
written down before it ran.

Result: 10 of 10 short-but-hard requests went to the cheap model. 10 of 10
long-but-easy requests went to the strong one.

The router was a prompt length gate. It was not measuring difficulty at all.

Three times I thought I had the cause. Three times the measurement disagreed.
I found two bugs in my own tooling on the way: a verdict that printed "score wins"
when the numbers showed the opposite, and a proxy that reported a clean result while
returning nothing at all. The only claim I kept is the one that survived a test built
to break it.

What I did not do: never deployed the cloud half. Never ran the paid classifier,
Jev, which is the thing all of this points at. Never measured money saved, only
latency. The harness as shipped only tests the free classifier.

Which is the next step. Swap the heuristic for a learned classifier and rerun the
same four cells. If it breaks the length pattern, the routing idea is worth the
cost. If it does not, then no classifier saves you and the honest answer is to
always use the cheap model.

That comparison is one run away and I have not done it yet.

If you have built a model router, how did you check it was actually routing?

P.S. Full repo, evaluation prompts and all, is in the first comment. Happy to hear
where this reasoning breaks.
