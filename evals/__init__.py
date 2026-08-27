"""Offline evaluation harness for PaperTrace's claim checker.

Software tests (`tests/`) prove the plumbing works. This package answers a
different question — whether the model's *verdicts* are right — by scoring a
real run against hand-labelled gold cases.

Nothing here runs in CI except `evals/tests/`, which is pure arithmetic on
fixtures. The live runner costs money and is a plain script, never a test.
"""
