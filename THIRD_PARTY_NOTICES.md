# Third-party research and software notices

No third-party simulator source, model weight, checkpoint, commercial order-book data, or private dataset is copied into this repository. The default engine is an original implementation written during OpenAI Build Week.

## Systems inspected

| System | Repository and inspected revision | License | Use in this project |
|---|---|---|---|
| ABIDES | https://github.com/abides-sim/abides @ `c4bf157678928934417aba6073eb0651aeaf6d15` | BSD-3-Clause | Architectural study only: discrete-event agents, exchange messages, latency. No code copied. |
| ABIDES-JPMC | https://github.com/jpmorganchase/abides-jpmc-public @ `f9cbe51342b7dedd9587e4e069040d68a5c6477f` | BSD-3-Clause | Modular-backend study only. Repository is archived. No code copied. |
| JAX-LOB | https://github.com/KangOxford/jax-lob @ `d1f596610b04a09941c7a1b609e5bab541ecfc98` | No license found | Concepts only. Source was not copied or adapted. |
| DeepMarket/TRADES | https://github.com/LeonardoBerti00/DeepMarket @ `8f1f89b7285c79a73f528b88b60f74ce58faadc4` | MIT | Responsive-order-flow and evaluation study only. No code, checkpoint, or LOBSTER-derived data copied. |
| MarS | https://github.com/microsoft/MarS @ `77e7845b1c6bfaa90a4a780df1519bdfaad17b7b` | MIT | Controllable-world, multiple-futures, and impact-evaluation study only. No code or private model used. |

## Research citations

- Byrd, Hybinette, and Balch, “ABIDES: Towards High-Fidelity Market Simulation for AI Research,” arXiv:1904.12066.
- Amrouni et al., “ABIDES-Gym: Gym Environments for Multi-Agent Discrete Event Simulation and Application to Financial Markets,” arXiv:2110.14771.
- Frey et al., “JAX-LOB: A GPU-Accelerated Limit Order Book Simulator,” arXiv:2308.13289.
- Berti, Prenkaj, and Velardi, “TRADES: Generating Realistic Market Simulations with Diffusion Models,” arXiv:2502.07071.
- Li et al., “MarS: a Financial Market Simulation Engine Powered by Generative Foundation Model,” arXiv:2409.07486.

If optional third-party code is integrated later, its exact source files, license text, modifications, and redistribution obligations must be added here before release.

