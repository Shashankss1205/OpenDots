# Deployment fixture maintainer

Investigate an incoming deployment report against the local fixture.
The desired state is at least one requested replica.
Propose a small local change and run the configured deployment check after it.
Do not contact a live Kubernetes cluster. Keep work as a branch and patch for review.
