* Signature: 0x11fd08368622b95b
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
 L  R11     
 G  R12     
 E  R13     
 L  R14     
 L  R15     
 G  R16     
 L  R17     
 G  R18     
 L  R19     
 G  R20     
 G  R21     
 L  R22     
 G  R23     
 L  R24     
 G  R25     
 L  R26     
 E  R27     
 E  R28     
 E  R29     
 E  R30     
 E  R31     
 E  R32     
 E  R33     
 E  R34     
 G  R35     
 L  R36     
 G  R37     
 L  R38     
 G  R39     
 G  R40     
 G  R41     
 G  R42     
 L  R43     
 G  R44     
 L  R45     
 G  R46     
 G  R47     
 G  R48     
 L  R49     
 G  R50     
 L  R51     
 G  R52     
 G  R53     
 L  R54     
 G  R55     
 L  R56     
 G  R57     
 G  R58     
 G  R59     
 G  R60     
 L  R61     
 G  R62     
 L  R63     
 G  R64     
 G  R65     
 L  R66     
 G  R67     
 L  R68     
 G  R69     
 G  R70     
 G  GC0     
 G  GC1     
 G  GC2     
 L  GC3     
 L  GC4     
 L  GC5     
COLUMNS
    MARKER    'MARKER'                 'INTORG'
    visit[0]  R6        1
    visit[0]  R7        -10.000001
    visit[0]  R8        -1
    visit[0]  R14       1
    visit[0]  R15       -4.000001
    visit[0]  R16       -1
    visit[0]  R21       -1
    visit[0]  R22       -3
    visit[0]  R29       -1
    visit[0]  R30       -1
    visit[1]  R6        1
    visit[1]  R9        -10.000001
    visit[1]  R10       -1
    visit[1]  R14       1
    visit[1]  R17       -4.000001
    visit[1]  R18       -1
    visit[1]  R23       -1
    visit[1]  R24       -3
    visit[1]  R31       -1
    visit[1]  R32       -1
    visit[2]  R6        1
    visit[2]  R11       -10.000001
    visit[2]  R12       -1
    visit[2]  R14       1
    visit[2]  R19       -4.000001
    visit[2]  R20       -1
    visit[2]  R25       -1
    visit[2]  R26       -3
    visit[2]  R33       -1
    visit[2]  R34       -1
    service_start_0  R35       1
    service_start_0  R40       -1
    service_start_0  R41       -1
    service_start_0  R47       -1
    service_start_1  R41       1
    service_start_1  R53       1
    service_start_1  R58       -1
    service_start_1  R59       -1
    service_start_2  R47       1
    service_start_2  R59       1
    service_start_2  R65       1
    service_start_2  R70       -1
    MARKER    'MARKER'                 'INTEND'
    causal_rank[0]  R21       1
    causal_rank[0]  R22       1
    causal_rank[0]  R42       -1
    causal_rank[0]  R48       -1
    causal_rank[1]  R23       1
    causal_rank[1]  R24       1
    causal_rank[1]  R42       1
    causal_rank[1]  R60       -1
    causal_rank[2]  R25       1
    causal_rank[2]  R26       1
    causal_rank[2]  R48       1
    causal_rank[2]  R60       1
    MARKER    'MARKER'                 'INTORG'
    departure  R35       -1
    departure  R53       -1
    departure  R65       -1
    departure  GC0       1
    departure  GC1       1
    departure  GC2       1
    return_complete  R0        1
    return_complete  R40       1
    return_complete  R58       1
    return_complete  R70       1
    return_complete  GC3       1
    return_complete  GC4       1
    return_complete  GC5       1
    delivery_batch[b1]  R1        1
    delivery_batch[b1]  R2        1
    delivery_batch[b2]  R1        1
    delivery_batch[b2]  R3        1
    delivery_batch[b3]  R1        1
    delivery_batch[b3]  R4        1
    MARKER    'MARKER'                 'INTEND'
    weight[0]  R7        1
    weight[0]  R8        1
    weight[0]  R36       1
    weight[0]  R37       1
    weight[0]  R43       -1
    weight[0]  R44       -1
    weight[0]  R49       -1
    weight[0]  R50       -1
    weight[1]  R9        1
    weight[1]  R10       1
    weight[1]  R43       1
    weight[1]  R44       1
    weight[1]  R54       1
    weight[1]  R55       1
    weight[1]  R61       -1
    weight[1]  R62       -1
    weight[2]  R11       1
    weight[2]  R12       1
    weight[2]  R49       1
    weight[2]  R50       1
    weight[2]  R61       1
    weight[2]  R62       1
    weight[2]  R66       1
    weight[2]  R67       1
    initial_weight  R5        1
    initial_weight  R36       -1
    initial_weight  R37       -1
    initial_weight  R54       -1
    initial_weight  R55       -1
    initial_weight  R66       -1
    initial_weight  R67       -1
    volume[0]  R15       1
    volume[0]  R16       1
    volume[0]  R38       1
    volume[0]  R39       1
    volume[0]  R45       -1
    volume[0]  R46       -1
    volume[0]  R51       -1
    volume[0]  R52       -1
    volume[1]  R17       1
    volume[1]  R18       1
    volume[1]  R45       1
    volume[1]  R46       1
    volume[1]  R56       1
    volume[1]  R57       1
    volume[1]  R63       -1
    volume[1]  R64       -1
    volume[2]  R19       1
    volume[2]  R20       1
    volume[2]  R51       1
    volume[2]  R52       1
    volume[2]  R63       1
    volume[2]  R64       1
    volume[2]  R68       1
    volume[2]  R69       1
    initial_volume  R13       1
    initial_volume  R38       -1
    initial_volume  R39       -1
    initial_volume  R56       -1
    initial_volume  R57       -1
    initial_volume  R68       -1
    initial_volume  R69       -1
    MARKER    'MARKER'                 'INTORG'
    arc[-1,0]  OBJ       1
    arc[-1,0]  R27       1
    arc[-1,0]  R29       1
    arc[-1,0]  R35       -10560
    arc[-1,0]  R36       9.000001
    arc[-1,0]  R37       -11.000001
    arc[-1,0]  R38       3.000001
    arc[-1,0]  R39       -5.000001
    arc[0,-2]  OBJ       1
    arc[0,-2]  R28       1
    arc[0,-2]  R30       1
    arc[0,-2]  R40       -1380
    arc[0,1]  R30       1
    arc[0,1]  R31       1
    arc[0,1]  R42       -4
    arc[0,1]  R43       9.000001
    arc[0,1]  R44       -11.000001
    arc[0,1]  R45       3.000001
    arc[0,1]  R46       -5.000001
    arc[0,2]  R30       1
    arc[0,2]  R33       1
    arc[0,2]  R48       -4
    arc[0,2]  R49       9.000001
    arc[0,2]  R50       -11.000001
    arc[0,2]  R51       3.000001
    arc[0,2]  R52       -5.000001
    arc[-1,1]  OBJ       1
    arc[-1,1]  R27       1
    arc[-1,1]  R31       1
    arc[-1,1]  R53       -6960
    arc[-1,1]  R54       9.000001
    arc[-1,1]  R55       -11.000001
    arc[-1,1]  R56       3.000001
    arc[-1,1]  R57       -5.000001
    arc[1,-2]  OBJ       1
    arc[1,-2]  R28       1
    arc[1,-2]  R32       1
    arc[1,-2]  R58       -4980
    arc[1,2]  R32       1
    arc[1,2]  R33       1
    arc[1,2]  R60       -4
    arc[1,2]  R61       9.000001
    arc[1,2]  R62       -11.000001
    arc[1,2]  R63       3.000001
    arc[1,2]  R64       -5.000001
    arc[-1,2]  OBJ       1
    arc[-1,2]  R27       1
    arc[-1,2]  R33       1
    arc[-1,2]  R65       -3360
    arc[-1,2]  R66       9.000001
    arc[-1,2]  R67       -11.000001
    arc[-1,2]  R68       3.000001
    arc[-1,2]  R69       -5.000001
    arc[2,-2]  OBJ       1
    arc[2,-2]  R28       1
    arc[2,-2]  R34       1
    arc[2,-2]  R70       -8580
    MARKER    'MARKER'                 'INTEND'
RHS
    RHS1      R0        11340
    RHS1      R1        1
    RHS1      R6        10.000001
    RHS1      R14       4.000001
    RHS1      R27       1
    RHS1      R28       1
    RHS1      R35       -10500
    RHS1      R36       10.000001
    RHS1      R37       -10.000001
    RHS1      R38       4.000001
    RHS1      R39       -4.000001
    RHS1      R40       -1200
    RHS1      R41       60
    RHS1      R42       -3
    RHS1      R43       10.000001
    RHS1      R44       -10.000001
    RHS1      R45       4.000001
    RHS1      R46       -4.000001
    RHS1      R47       60
    RHS1      R48       -3
    RHS1      R49       10.000001
    RHS1      R50       -10.000001
    RHS1      R51       4.000001
    RHS1      R52       -4.000001
    RHS1      R53       -6900
    RHS1      R54       10.000001
    RHS1      R55       -10.000001
    RHS1      R56       4.000001
    RHS1      R57       -4.000001
    RHS1      R58       -4800
    RHS1      R59       60
    RHS1      R60       -3
    RHS1      R61       10.000001
    RHS1      R62       -10.000001
    RHS1      R63       4.000001
    RHS1      R64       -4.000001
    RHS1      R65       -3300
    RHS1      R66       10.000001
    RHS1      R67       -10.000001
    RHS1      R68       4.000001
    RHS1      R69       -4.000001
    RHS1      R70       -8400
    RHS1      GC0       480
    RHS1      GC1       4080
    RHS1      GC2       7680
    RHS1      GC3       9000
    RHS1      GC4       9000
    RHS1      GC5       9000
BOUNDS
 BV BND1      visit[0]
 BV BND1      visit[1]
 BV BND1      visit[2]
 LO BND1      service_start_0  900
 UP BND1      service_start_0  1200
 LO BND1      service_start_1  4500
 UP BND1      service_start_1  4800
 LO BND1      service_start_2  8100
 UP BND1      service_start_2  8400
 UP BND1      causal_rank[0]  3
 UP BND1      causal_rank[1]  3
 UP BND1      causal_rank[2]  3
 UP BND1      departure  11400
 UP BND1      return_complete  11400
 BV BND1      delivery_batch[b1]
 BV BND1      delivery_batch[b2]
 BV BND1      delivery_batch[b3]
 UP BND1      weight[0]  10.000001
 UP BND1      weight[1]  10.000001
 UP BND1      weight[2]  10.000001
 UP BND1      initial_weight  10.000001
 UP BND1      volume[0]  4.000001
 UP BND1      volume[1]  4.000001
 UP BND1      volume[2]  4.000001
 UP BND1      initial_volume  4.000001
 BV BND1      arc[-1,0]
 BV BND1      arc[0,-2]
 BV BND1      arc[0,1]
 BV BND1      arc[0,2]
 BV BND1      arc[-1,1]
 BV BND1      arc[1,-2]
 BV BND1      arc[1,2]
 BV BND1      arc[-1,2]
 BV BND1      arc[2,-2]
INDICATORS
 IF GC0       delivery_batch[b1]  1
 IF GC1       delivery_batch[b2]  1
 IF GC2       delivery_batch[b3]  1
 IF GC3       visit[0]  1
 IF GC4       visit[1]  1
 IF GC5       visit[2]  1
ENDATA
