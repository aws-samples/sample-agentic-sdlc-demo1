"""Handoff gate package — generalized inter-segment boundary nodes.

The handoff gate halts the SDLC graph at a segment's outbound boundary so the
owning persona can confirm the handoff to the next persona. Because every
segment with a successor gets its own gate, the mechanism generalizes from the
BA->SA case to any future persona chain (design Section 5b, Req 6.2).
"""
