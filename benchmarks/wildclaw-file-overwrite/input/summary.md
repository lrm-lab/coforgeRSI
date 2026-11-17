Deep Residual Learning for Image Recognition — Summary

Paper: He, Kaiming; Zhang, Xiangyu; Ren, Shaoqing; Sun, Jian. arXiv:1512.03385 (2015).

Core idea
- Learn residual functions rather than directly learning unreferenced mappings. Reformulates the desired mapping as F(x) = H(x) - x, so H(x) = x + F(x). This facilitates training of very deep networks by mitigating vanishing/exploding gradient problems.

Key concepts
- Identity skip connections: Directly add input x to the output of a few stacked layers, enabling gradients to flow through the identity path.
- Residual blocks: Small building blocks (typically 2 or 3 layers) that learn residual functions. Enable construction of very deep networks with improved trainability.
- Reaching deeper architectures: Demonstrates training surprisingly deep networks (e.g., 34-layer, and later variants) with substantial accuracy gains over plain networks.
- Optimization insights: Deeper networks with residual connections converge faster and generalize better, even without sophisticated regularization tweaks.

Architectural highlights
- Block design: Quick 2-layer bottleneck-like blocks (Conv-BN-ReLU-Conv-BN) with a skip connection. Optional projection to match dimensions when downsampling.
- Depth vs. width: Deeper architectures yield better performance, with the residual design enabling training of hundreds of layers in later work.
- Initialization and normalization: Careful initialization and batch normalization were used to stabilize training.

Empirical results
- CIFAR-10/100: Residual networks substantially improve accuracy over plain networks at comparable depths.
- ImageNet: Deep residual nets (e.g., 34-layer) outperform traditional CNNs, setting new state-of-the-art at the time.
- Ablations: Demonstrates the importance of skip connections and how removing them degrades performance and hampers optimization.

Innovation and impact
- Introduced the residual learning framework that made training very deep networks practical and effective.
- Popularized the use of short, identity-mapped skip connections in deep CNNs.
- Laid groundwork for subsequent architectures (e.g., deeper ResNets, bottleneck variants) that became standard in computer vision.

Notes
- The idea has since become foundational in many deep learning architectures beyond CNNs and image recognition.
