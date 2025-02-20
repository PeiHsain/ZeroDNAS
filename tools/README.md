1. train_izdnas_all.py => main searching code of IzeroDNAS (ZeroDNAS + warm-up) with depth loss and latency loss

2. train_izdnas.py => main searching code of IzeroDNAS (ZeroDNAS + warm-up)

3. train_dnas.py => YOLO-DNAS (FBNet and ScaledYOLOv4)
    - search space = 12^8
    - train config = train_dnas.yaml
        - BottleneckCSP:  0
        - BottleneckCSP2: 1
        - SEARCH_RESOLUTION: 416

4. train.py => original zero-dans (but search space increase)
    - search space => 12^8
    - train config = **train_zdnasV2.yaml**
        - BottleneckCSP:  0
        - BottleneckCSP2: 1
        - SEARCH_RESOLUTION: 288
        
5. train.py => original zero-dans
    - search space => 12^4 * 4 ^ 4
    - train config = **train_zdnas.yaml**
        - BottleneckCSP:  0
        - BottleneckCSP2: 0
        - SEARCH_RESOLUTION: 288

6. train_sensitive.py => my experiment with foreground and background sensitivity
    - search space => 12^8
