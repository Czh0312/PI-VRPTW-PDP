* Signature: 0xdda101b1b9c1de52
NAME M5_complete_E1_route_inventory_relaxation
ROWS
 N  OBJ
 G  R0      
 L  R1      
 L  R2      
 L  R3      
 L  R4      
 L  R5      
 G  R6      
 L  R7      
 L  R8      
 L  R9      
 G  R10     
 L  R11     
 L  R12     
 L  R13     
 L  R14     
 E  R15     
 E  R16     
 L  R17     
 L  R18     
 L  R19     
 L  R20     
 G  R21     
 G  R22     
 G  R23     
 E  R24     
 L  R25     
 G  R26     
 G  R27     
 G  R28     
 E  R29     
 E  R30     
 E  R31     
 G  clean_stock_b1
 G  clean_stock_b2
 G  R34     
 G  R35     
 E  R36     
 G  R37     
 L  R38     
 L  R39     
 G  R40     
 L  R41     
 L  R42     
 E  R43     
 E  R44     
 E  R45     
 E  R46     
 E  R47     
 E  R48     
 E  pool_cover_A_d
 E  pool_cover_A_p
 E  pool_cover_B_d
 E  pool_cover_B_p
 E  R53     
 E  R54     
 E  R55     
 E  R56     
 E  R57     
 G  GC0     
 L  GC1     
 G  GC2     
 L  GC3     
 G  GC4     
 L  GC5     
 G  GC6     
 L  GC7     
 G  GC8     
 G  GC9     
 G  GC10    
 G  GC11    
 G  GC12    
 G  GC13    
 E  GC14    
 E  GC15    
 G  GC16    
 G  GC17    
 G  GC18    
 G  GC19    
 G  GC20    
 G  GC21    
 G  GC22    
 G  GC23    
 G  GC24    
 G  GC25    
 G  GC26    
COLUMNS
    MARKER    'MARKER'                 'INTORG'
    route[0]  R0        -1
    route[0]  R1        -2
    route[0]  R2        -500.000001
    route[0]  R3        -500.000001
    route[0]  R14       1
    route[0]  R17       1
    route[1]  R4        -500.000001
    route[1]  R5        -500.000001
    route[1]  R14       1
    route[1]  R15       1
    route[2]  R6        -1
    route[2]  R7        -2
    route[2]  R8        -500.000001
    route[2]  R9        -500.000001
    route[2]  R14       1
    route[2]  R15       1
    route[2]  R17       1
    route[3]  R10       -1
    route[3]  R11       -2
    route[3]  R12       -500.000001
    route[3]  R13       -500.000001
    route[3]  R18       1
    route[3]  R19       1
    quantity_0_S  R0        1
    quantity_0_S  R1        1
    quantity_0_S  R2        1
    quantity_0_S  R3        1
    quantity_0_S  R30       1
    quantity_0_S  clean_stock_b2  1
    quantity_2_S  R6        1
    quantity_2_S  R7        1
    quantity_2_S  R8        1
    quantity_2_S  R9        1
    quantity_2_S  R30       1
    quantity_2_S  clean_stock_b2  1
    quantity_3_S  R10       1
    quantity_3_S  R11       1
    quantity_3_S  R12       1
    quantity_3_S  R13       1
    quantity_3_S  R31       1
    supply_S_b1  R16       1
    supply_S_b1  R20       1
    supply_S_b1  R21       -1
    supply_S_b1  R25       1
    supply_S_b1  R26       -1
    supply_S_b1  GC16      -1
    delivery_A  R21       1
    delivery_A  R22       -1
    delivery_A  GC8       1
    delivery_A  GC17      -1
    pickup_time_A  R22       1
    pickup_time_A  R23       -1
    pickup_time_A  GC12      -1
    pickup_time_A  GC19      1
    pickup_time_A  GC21      -1
    pickup_time_A  GC25      1
    e2_return_A  R23       1
    e2_return_A  GC0       1
    e2_return_A  GC1       1
    e2_return_A  GC2       1
    e2_return_A  GC3       1
    e2_return_A  GC14      1
    cutoff_A_b1  R24       1
    cutoff_A_b1  R30       -1
    cutoff_A_b2  R24       1
    cutoff_A_b2  R31       -1
    delivery_B  R26       1
    delivery_B  R27       -1
    delivery_B  GC17      1
    delivery_B  GC19      -1
    delivery_B  GC23      -1
    pickup_time_B  R27       1
    pickup_time_B  R28       -1
    pickup_time_B  GC10      -1
    pickup_time_B  GC21      1
    pickup_time_B  GC23      1
    pickup_time_B  GC25      -1
    e2_return_B  R28       1
    e2_return_B  GC4       1
    e2_return_B  GC5       1
    e2_return_B  GC6       1
    e2_return_B  GC7       1
    e2_return_B  GC15      1
    cutoff_B_b1  R29       1
    cutoff_B_b1  R30       -1
    cutoff_B_b2  R29       1
    cutoff_B_b2  R31       -1
    MARKER    'MARKER'                 'INTEND'
    pool_task_rank_A_d  R34       -1
    pool_task_rank_A_d  GC9       1
    pool_task_rank_A_d  GC18      -1
    pool_task_rank_A_p  R34       1
    pool_task_rank_A_p  GC13      -1
    pool_task_rank_A_p  GC20      1
    pool_task_rank_A_p  GC22      -1
    pool_task_rank_A_p  GC26      1
    pool_task_rank_B_d  R35       -1
    pool_task_rank_B_d  GC18      1
    pool_task_rank_B_d  GC20      -1
    pool_task_rank_B_d  GC24      -1
    pool_task_rank_B_p  R35       1
    pool_task_rank_B_p  GC11      -1
    pool_task_rank_B_p  GC22      1
    pool_task_rank_B_p  GC24      1
    pool_task_rank_B_p  GC26      -1
    MARKER    'MARKER'                 'INTORG'
    pool_choice_0[0]  OBJ       6
    pool_choice_0[0]  R36       1
    pool_choice_0[0]  R43       -1
    pool_choice_0[0]  R44       -1
    pool_choice_0[0]  R46       -1
    pool_choice_0[0]  R47       -1
    pool_choice_0[0]  R48       -1
    pool_choice_0[0]  pool_cover_A_d  1
    pool_choice_0[0]  pool_cover_A_p  1
    pool_choice_0[0]  pool_cover_B_d  1
    pool_choice_0[0]  pool_cover_B_p  1
    pool_choice_0[0]  R53       -1
    pool_choice_0[0]  R54       -1
    pool_choice_0[0]  R55       -1
    pool_choice_0[1]  OBJ       4
    pool_choice_0[1]  R36       1
    pool_choice_0[1]  R43       -1
    pool_choice_0[1]  R45       -1
    pool_choice_0[1]  R46       -1
    pool_choice_0[1]  R47       -1
    pool_choice_0[1]  R48       -1
    pool_choice_0[1]  pool_cover_A_d  1
    pool_choice_0[1]  pool_cover_A_p  1
    pool_choice_0[1]  pool_cover_B_d  1
    pool_choice_0[1]  pool_cover_B_p  1
    pool_choice_0[1]  R53       -1
    pool_choice_0[1]  R56       -1
    pool_choice_0[1]  R57       -1
    pool_used_0  R36       -1
    pool_used_0  R38       -125
    pool_used_0  R39       -125
    pool_used_0  R40       -1
    pool_used_0  R41       -6
    pool_used_0  R42       -6
    pool_start_0  R37       1
    pool_start_0  R38       1
    pool_start_0  GC8       -1
    pool_start_0  GC16      1
    pool_end_0  R39       1
    pool_end_0  GC10      1
    pool_end_0  GC12      1
    pool_end_0  GC14      -1
    pool_end_0  GC15      -1
    MARKER    'MARKER'                 'INTEND'
    pool_start_rank_0  R40       1
    pool_start_rank_0  R41       1
    pool_start_rank_0  GC9       -1
    pool_end_rank_0  R42       1
    pool_end_rank_0  GC11      1
    pool_end_rank_0  GC13      1
    MARKER    'MARKER'                 'INTORG'
    pool_first_0_A_d  R43       1
    pool_last_0_B_p  R44       1
    pool_last_0_A_p  R45       1
    pool_pickup_0_A  R46       1
    pool_pickup_0_B  R47       1
    pool_batch_0_b1  R48       1
    pool_link_0  R53       1
    pool_link_1  R54       1
    pool_link_2  R55       1
    pool_link_3  R56       1
    pool_link_4  R57       1
    MARKER    'MARKER'                 'INTEND'
RHS
    RHS1      R14       1
    RHS1      R15       1
    RHS1      R17       1
    RHS1      R18       1
    RHS1      R19       1
    RHS1      R20       9
    RHS1      R21       1
    RHS1      R22       5
    RHS1      R23       1
    RHS1      R24       1
    RHS1      R25       8
    RHS1      R26       2
    RHS1      R27       5
    RHS1      R28       2
    RHS1      R29       1
    RHS1      R34       1
    RHS1      R35       1
    RHS1      pool_cover_A_d  1
    RHS1      pool_cover_A_p  1
    RHS1      pool_cover_B_d  1
    RHS1      pool_cover_B_p  1
    RHS1      GC0       0
    RHS1      GC1       30
    RHS1      GC2       31
    RHS1      GC3       90
    RHS1      GC4       0
    RHS1      GC5       30
    RHS1      GC6       31
    RHS1      GC7       90
    RHS1      GC8       1
    RHS1      GC9       1
    RHS1      GC10      2
    RHS1      GC11      1
    RHS1      GC12      1
    RHS1      GC13      1
    RHS1      GC14      0
    RHS1      GC15      0
    RHS1      GC16      0
    RHS1      GC17      1
    RHS1      GC18      1
    RHS1      GC19      1
    RHS1      GC20      1
    RHS1      GC21      1
    RHS1      GC22      1
    RHS1      GC23      0
    RHS1      GC24      1
    RHS1      GC25      1
    RHS1      GC26      1
BOUNDS
 BV BND1      route[0]
 BV BND1      route[1]
 BV BND1      route[2]
 BV BND1      route[3]
 UP BND1      quantity_0_S  2
 UP BND1      quantity_2_S  2
 UP BND1      quantity_3_S  2
 UP BND1      supply_S_b1  125
 UP BND1      delivery_A  10
 LO BND1      pickup_time_A  20
 UP BND1      pickup_time_A  25
 UP BND1      e2_return_A  90
 BV BND1      cutoff_A_b1
 BV BND1      cutoff_A_b2
 UP BND1      delivery_B  10
 LO BND1      pickup_time_B  20
 UP BND1      pickup_time_B  25
 UP BND1      e2_return_B  90
 BV BND1      cutoff_B_b1
 BV BND1      cutoff_B_b2
 UP BND1      pool_task_rank_A_d  6
 UP BND1      pool_task_rank_A_p  6
 UP BND1      pool_task_rank_B_d  6
 UP BND1      pool_task_rank_B_p  6
 BV BND1      pool_choice_0[0]
 BV BND1      pool_choice_0[1]
 BV BND1      pool_used_0
 UP BND1      pool_start_0  125
 UP BND1      pool_end_0  125
 UP BND1      pool_start_rank_0  6
 UP BND1      pool_end_rank_0  6
 BV BND1      pool_first_0_A_d
 BV BND1      pool_last_0_B_p
 BV BND1      pool_last_0_A_p
 BV BND1      pool_pickup_0_A
 BV BND1      pool_pickup_0_B
 BV BND1      pool_batch_0_b1
 BV BND1      pool_link_0
 BV BND1      pool_link_1
 BV BND1      pool_link_2
 BV BND1      pool_link_3
 BV BND1      pool_link_4
INDICATORS
 IF GC0       cutoff_A_b1  1
 IF GC1       cutoff_A_b1  1
 IF GC2       cutoff_A_b2  1
 IF GC3       cutoff_A_b2  1
 IF GC4       cutoff_B_b1  1
 IF GC5       cutoff_B_b1  1
 IF GC6       cutoff_B_b2  1
 IF GC7       cutoff_B_b2  1
 IF GC8       pool_first_0_A_d  1
 IF GC9       pool_first_0_A_d  1
 IF GC10      pool_last_0_B_p  1
 IF GC11      pool_last_0_B_p  1
 IF GC12      pool_last_0_A_p  1
 IF GC13      pool_last_0_A_p  1
 IF GC14      pool_pickup_0_A  1
 IF GC15      pool_pickup_0_B  1
 IF GC16      pool_batch_0_b1  1
 IF GC17      pool_link_0  1
 IF GC18      pool_link_0  1
 IF GC19      pool_link_1  1
 IF GC20      pool_link_1  1
 IF GC21      pool_link_2  1
 IF GC22      pool_link_2  1
 IF GC23      pool_link_3  1
 IF GC24      pool_link_3  1
 IF GC25      pool_link_4  1
 IF GC26      pool_link_4  1
ENDATA
