* Signature: 0x5999ddadda764d72
NAME M5_relaxed_E2_trip_pricing
ROWS
 N  OBJ
 L  R0      
 L  R1      
 L  R2      
 L  R3      
 L  R4      
 E  R5      
 L  R6      
 L  R7      
 G  R8      
 L  R9      
 G  R10     
 E  R11     
 L  R12     
 L  R13     
 G  R14     
 L  R15     
 G  R16     
 G  R17     
 L  R18     
 G  R19     
 L  R20     
 E  R21     
 E  R22     
 E  R23     
 E  R24     
 E  R25     
 E  R26     
 G  R27     
 L  R28     
 G  R29     
 L  R30     
 G  R31     
 G  R32     
 G  R33     
 G  R34     
 L  R35     
 G  R36     
 L  R37     
 G  R38     
 G  R39     
 L  R40     
 G  R41     
 L  R42     
 G  R43     
 G  R44     
 G  GC0     
 G  GC1     
 G  GC2     
 L  GC3     
 L  GC4     
COLUMNS
    MARKER    'MARKER'                 'INTORG'
    visit[0]  R6        4
    visit[0]  R7        -180.000001
    visit[0]  R8        -4
    visit[0]  R12       2
    visit[0]  R13       -6.000001
    visit[0]  R14       -2
    visit[0]  R17       -1
    visit[0]  R18       -2
    visit[0]  R23       -1
    visit[0]  R24       -1
    visit[1]  R6        4
    visit[1]  R9        -180.000001
    visit[1]  R10       -4
    visit[1]  R12       2
    visit[1]  R15       -6.000001
    visit[1]  R16       -2
    visit[1]  R19       -1
    visit[1]  R20       -2
    visit[1]  R25       -1
    visit[1]  R26       -1
    service_start_0  R27       1
    service_start_0  R32       -1
    service_start_0  R33       -1
    service_start_1  R33       1
    service_start_1  R39       1
    service_start_1  R44       -1
    MARKER    'MARKER'                 'INTEND'
    causal_rank[0]  R17       1
    causal_rank[0]  R18       1
    causal_rank[0]  R34       -1
    causal_rank[1]  R19       1
    causal_rank[1]  R20       1
    causal_rank[1]  R34       1
    MARKER    'MARKER'                 'INTORG'
    departure  R27       -1
    departure  R39       -1
    departure  GC0       1
    departure  GC1       1
    departure  GC2       1
    return_complete  R0        1
    return_complete  R32       1
    return_complete  R44       1
    return_complete  GC3       1
    return_complete  GC4       1
    delivery_batch[b1]  R1        1
    delivery_batch[b1]  R2        1
    delivery_batch[b2]  R1        1
    delivery_batch[b2]  R3        1
    delivery_batch[b3]  R1        1
    delivery_batch[b3]  R4        1
    MARKER    'MARKER'                 'INTEND'
    weight[0]  R7        1
    weight[0]  R8        1
    weight[0]  R28       1
    weight[0]  R29       1
    weight[0]  R35       -1
    weight[0]  R36       -1
    weight[1]  R9        1
    weight[1]  R10       1
    weight[1]  R35       1
    weight[1]  R36       1
    weight[1]  R40       1
    weight[1]  R41       1
    initial_weight  R5        1
    initial_weight  R28       -1
    initial_weight  R29       -1
    initial_weight  R40       -1
    initial_weight  R41       -1
    volume[0]  R13       1
    volume[0]  R14       1
    volume[0]  R30       1
    volume[0]  R31       1
    volume[0]  R37       -1
    volume[0]  R38       -1
    volume[1]  R15       1
    volume[1]  R16       1
    volume[1]  R37       1
    volume[1]  R38       1
    volume[1]  R42       1
    volume[1]  R43       1
    initial_volume  R11       1
    initial_volume  R30       -1
    initial_volume  R31       -1
    initial_volume  R42       -1
    initial_volume  R43       -1
    MARKER    'MARKER'                 'INTORG'
    arc[-1,0]  OBJ       3.8275318418009277e+01
    arc[-1,0]  R21       1
    arc[-1,0]  R23       1
    arc[-1,0]  R27       -40749
    arc[-1,0]  R28       176.000001
    arc[-1,0]  R29       -184.000001
    arc[-1,0]  R30       4.000001
    arc[-1,0]  R31       -8.0000010000000010e+00
    arc[0,-2]  OBJ       3.8275318418009277e+01
    arc[0,-2]  R22       1
    arc[0,-2]  R24       1
    arc[0,-2]  R32       -13569
    arc[0,1]  OBJ       3.0805843601498726e+01
    arc[0,1]  R24       1
    arc[0,1]  R25       1
    arc[0,1]  R34       -3
    arc[0,1]  R35       176.000001
    arc[0,1]  R36       -184.000001
    arc[0,1]  R37       4.000001
    arc[0,1]  R38       -8.0000010000000010e+00
    arc[-1,1]  OBJ       8.48528137423857
    arc[-1,1]  R21       1
    arc[-1,1]  R25       1
    arc[-1,1]  R39       -28119
    arc[-1,1]  R40       176.000001
    arc[-1,1]  R41       -184.000001
    arc[-1,1]  R42       4.000001
    arc[-1,1]  R43       -8.0000010000000010e+00
    arc[1,-2]  OBJ       8.48528137423857
    arc[1,-2]  R22       1
    arc[1,-2]  R26       1
    arc[1,-2]  R44       -23475
    MARKER    'MARKER'                 'INTEND'
RHS
    RHS1      R0        75540
    RHS1      R1        1
    RHS1      R6        180.000001
    RHS1      R12       6.000001
    RHS1      R21       1
    RHS1      R22       1
    RHS1      R27       -39600
    RHS1      R28       180.000001
    RHS1      R29       -180.000001
    RHS1      R30       6.000001
    RHS1      R31       -6.0000010000000010e+00
    RHS1      R32       -11760
    RHS1      R33       1525
    RHS1      R34       -2
    RHS1      R35       180.000001
    RHS1      R36       -180.000001
    RHS1      R37       6.000001
    RHS1      R38       -6.0000010000000010e+00
    RHS1      R39       -27864
    RHS1      R40       180.000001
    RHS1      R41       -180.000001
    RHS1      R42       6.000001
    RHS1      R43       -6.0000010000000010e+00
    RHS1      R44       -22560
    RHS1      GC0       31281
    RHS1      GC1       45681
    RHS1      GC2       60081
    RHS1      GC3       71100
    RHS1      GC4       71100
BOUNDS
 BV BND1      visit[0]
 BV BND1      visit[1]
 LO BND1      service_start_0  36000
 UP BND1      service_start_0  40560
 LO BND1      service_start_1  47736
 UP BND1      service_start_1  51360
 UP BND1      causal_rank[0]  2
 UP BND1      causal_rank[1]  2
 LO BND1      departure  28800
 UP BND1      departure  75600
 LO BND1      return_complete  28800
 UP BND1      return_complete  75600
 BV BND1      delivery_batch[b1]
 BV BND1      delivery_batch[b2]
 BV BND1      delivery_batch[b3]
 UP BND1      weight[0]  180.000001
 UP BND1      weight[1]  180.000001
 UP BND1      initial_weight  180.000001
 UP BND1      volume[0]  6.000001
 UP BND1      volume[1]  6.000001
 UP BND1      initial_volume  6.000001
 BV BND1      arc[-1,0]
 BV BND1      arc[0,-2]
 BV BND1      arc[0,1]
 BV BND1      arc[-1,1]
 BV BND1      arc[1,-2]
INDICATORS
 IF GC0       delivery_batch[b1]  1
 IF GC1       delivery_batch[b2]  1
 IF GC2       delivery_batch[b3]  1
 IF GC3       visit[0]  1
 IF GC4       visit[1]  1
ENDATA
