* Signature: 0xb8ae11e976d25b90
NAME M5_relaxed_E2_trip_pricing
ROWS
 N  OBJ
 L  R0      
 L  R1      
 L  R2      
 L  R3      
 E  R4      
 L  R5      
 L  R6      
 G  R7      
 L  R8      
 G  R9      
 E  R10     
 L  R11     
 L  R12     
 G  R13     
 L  R14     
 G  R15     
 G  R16     
 L  R17     
 G  R18     
 L  R19     
 E  R20     
 E  R21     
 E  R22     
 E  R23     
 E  R24     
 E  R25     
 G  R26     
 L  R27     
 G  R28     
 L  R29     
 G  R30     
 G  R31     
 G  R32     
 G  R33     
 L  R34     
 G  R35     
 L  R36     
 G  R37     
 G  R38     
 L  R39     
 G  R40     
 L  R41     
 G  R42     
 G  R43     
 G  GC0     
 G  GC1     
 L  GC2     
 L  GC3     
COLUMNS
    MARKER    'MARKER'                 'INTORG'
    visit[0]  R5        1
    visit[0]  R6        -10.000001
    visit[0]  R7        -1
    visit[0]  R11       1
    visit[0]  R12       -4.000001
    visit[0]  R13       -1
    visit[0]  R16       -1
    visit[0]  R17       -2
    visit[0]  R22       -1
    visit[0]  R23       -1
    visit[1]  R5        1
    visit[1]  R8        -10.000001
    visit[1]  R9        -1
    visit[1]  R11       1
    visit[1]  R14       -4.000001
    visit[1]  R15       -1
    visit[1]  R18       -1
    visit[1]  R19       -2
    visit[1]  R24       -1
    visit[1]  R25       -1
    service_start_0  R26       1
    service_start_0  R31       -1
    service_start_0  R32       -1
    service_start_1  R32       1
    service_start_1  R38       1
    service_start_1  R43       -1
    MARKER    'MARKER'                 'INTEND'
    causal_rank[0]  R16       1
    causal_rank[0]  R17       1
    causal_rank[0]  R33       -1
    causal_rank[1]  R18       1
    causal_rank[1]  R19       1
    causal_rank[1]  R33       1
    MARKER    'MARKER'                 'INTORG'
    departure  R26       -1
    departure  R38       -1
    departure  GC0       1
    departure  GC1       1
    return_complete  R0        1
    return_complete  R31       1
    return_complete  R43       1
    return_complete  GC2       1
    return_complete  GC3       1
    delivery_batch[b1]  R1        1
    delivery_batch[b1]  R2        1
    delivery_batch[b2]  R1        1
    delivery_batch[b2]  R3        1
    MARKER    'MARKER'                 'INTEND'
    weight[0]  R6        1
    weight[0]  R7        1
    weight[0]  R27       1
    weight[0]  R28       1
    weight[0]  R34       -1
    weight[0]  R35       -1
    weight[1]  R8        1
    weight[1]  R9        1
    weight[1]  R34       1
    weight[1]  R35       1
    weight[1]  R39       1
    weight[1]  R40       1
    initial_weight  R4        1
    initial_weight  R27       -1
    initial_weight  R28       -1
    initial_weight  R39       -1
    initial_weight  R40       -1
    volume[0]  R12       1
    volume[0]  R13       1
    volume[0]  R29       1
    volume[0]  R30       1
    volume[0]  R36       -1
    volume[0]  R37       -1
    volume[1]  R14       1
    volume[1]  R15       1
    volume[1]  R36       1
    volume[1]  R37       1
    volume[1]  R41       1
    volume[1]  R42       1
    initial_volume  R10       1
    initial_volume  R29       -1
    initial_volume  R30       -1
    initial_volume  R41       -1
    initial_volume  R42       -1
    MARKER    'MARKER'                 'INTORG'
    arc[-1,0]  OBJ       1
    arc[-1,0]  R20       1
    arc[-1,0]  R22       1
    arc[-1,0]  R26       -6960
    arc[-1,0]  R27       9.000001
    arc[-1,0]  R28       -11.000001
    arc[-1,0]  R29       3.000001
    arc[-1,0]  R30       -5.000001
    arc[0,-2]  OBJ       1
    arc[0,-2]  R21       1
    arc[0,-2]  R23       1
    arc[0,-2]  R31       -1380
    arc[0,1]  R23       1
    arc[0,1]  R24       1
    arc[0,1]  R33       -3
    arc[0,1]  R34       9.000001
    arc[0,1]  R35       -11.000001
    arc[0,1]  R36       3.000001
    arc[0,1]  R37       -5.000001
    arc[-1,1]  OBJ       1
    arc[-1,1]  R20       1
    arc[-1,1]  R24       1
    arc[-1,1]  R38       -3180
    arc[-1,1]  R39       9.000001
    arc[-1,1]  R40       -11.000001
    arc[-1,1]  R41       3.000001
    arc[-1,1]  R42       -5.000001
    arc[1,-2]  OBJ       1
    arc[1,-2]  R21       1
    arc[1,-2]  R25       1
    arc[1,-2]  R43       -5280
    MARKER    'MARKER'                 'INTEND'
RHS
    RHS1      R0        7740
    RHS1      R1        1
    RHS1      R5        10.000001
    RHS1      R11       4.000001
    RHS1      R20       1
    RHS1      R21       1
    RHS1      R26       -6900
    RHS1      R27       10.000001
    RHS1      R28       -10.000001
    RHS1      R29       4.000001
    RHS1      R30       -4.000001
    RHS1      R31       -1200
    RHS1      R32       60
    RHS1      R33       -2
    RHS1      R34       10.000001
    RHS1      R35       -10.000001
    RHS1      R36       4.000001
    RHS1      R37       -4.000001
    RHS1      R38       -3120
    RHS1      R39       10.000001
    RHS1      R40       -10.000001
    RHS1      R41       4.000001
    RHS1      R42       -4.000001
    RHS1      R43       -5100
    RHS1      GC0       480
    RHS1      GC1       4080
    RHS1      GC2       5400
    RHS1      GC3       5400
BOUNDS
 BV BND1      visit[0]
 BV BND1      visit[1]
 LO BND1      service_start_0  900
 UP BND1      service_start_0  1200
 LO BND1      service_start_1  4680
 UP BND1      service_start_1  5100
 UP BND1      causal_rank[0]  2
 UP BND1      causal_rank[1]  2
 UP BND1      departure  7800
 UP BND1      return_complete  7800
 BV BND1      delivery_batch[b1]
 BV BND1      delivery_batch[b2]
 UP BND1      weight[0]  10.000001
 UP BND1      weight[1]  10.000001
 UP BND1      initial_weight  10.000001
 UP BND1      volume[0]  4.000001
 UP BND1      volume[1]  4.000001
 UP BND1      initial_volume  4.000001
 BV BND1      arc[-1,0]
 BV BND1      arc[0,-2]
 BV BND1      arc[0,1]
 BV BND1      arc[-1,1]
 BV BND1      arc[1,-2]
INDICATORS
 IF GC0       delivery_batch[b1]  1
 IF GC1       delivery_batch[b2]  1
 IF GC2       visit[0]  1
 IF GC3       visit[1]  1
ENDATA
