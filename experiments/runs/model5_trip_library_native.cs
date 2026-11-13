// Complete reference walk. Binary protocol is little endian; no route pruning.
using System;
using System.IO;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;

public static class TripOracle {
    struct Entry { public double Distance; public int Count; public long A, B, C, D; }
    static int N;
    static long Start, End, Unload, Turnaround, Prefixes;
    static double WeightCap, VolumeCap, Limit, LastReport;
    static long[,] Travel;
    static double[,] Distance;
    static long[] Dlo, Dhi, Plo, Phi, Service, Use, Ready, Path;
    static double[] Dw, Dv, Pw, Pv;
    static bool Expired;
    static string ProgressPath;
    static Stopwatch Clock;
    static Dictionary<long, Entry> Columns = new Dictionary<long, Entry>();

    static void Report() {
        string value = "{\"prefixes\":"+Prefixes+",\"columns\":"+Columns.Count+"}";
        try { File.WriteAllText(ProgressPath, value); } catch (IOException) { }
        LastReport = Clock.Elapsed.TotalSeconds;
    }

    static void Walk(int at, long clock, long mask, long remaining, long chosen,
                     double weight, double volume, double length, int depth) {
        if (Expired) return;
        Prefixes++;
        if ((Prefixes & 131071) == 0) {
            double now = Clock.Elapsed.TotalSeconds;
            if (now-LastReport >= 5) Report();
            if (now >= Limit) { Expired = true; return; }
        }
        if (remaining == 0 && mask != 0 && clock+Travel[at,N]+Unload+Turnaround <= End) {
            double cost = length+Distance[at,N];
            Entry old;
            if (!Columns.TryGetValue(mask, out old) || cost < old.Distance) {
                Entry entry = new Entry(); entry.Distance = cost; entry.Count = depth;
                for (int j=0; j<depth; j++) {
                    long code = Path[j] << (6*(j % 10));
                    if (j<10) entry.A |= code;
                    else if (j<20) entry.B |= code;
                    else if (j<30) entry.C |= code;
                    else entry.D |= code;
                }
                Columns[mask] = entry;
            }
        }
        for (int i=0; i<N; i++) {
            long bit = 1L << i;
            if ((remaining & bit) == 0) continue;
            long arrive = Math.Max(clock+Travel[at,i], Dlo[i]);
            if (arrive > Dhi[i]) continue;
            Ready[i] = arrive; Path[depth] = i+1;
            Walk(i, arrive+Service[i], mask | bit, remaining ^ bit, chosen,
                 weight-Dw[i], volume-Dv[i], length+Distance[at,i], depth+1);
            Ready[i] = -1;
        }
        for (int i=0; i<N; i++) {
            long bit = 1L << i, pickup = 1L << (N+i);
            if ((mask & pickup) != 0 || (remaining & bit) != 0) continue;
            if (weight+Pw[i] > WeightCap+1e-8 || volume+Pv[i] > VolumeCap+1e-8) continue;
            long delivered = (chosen & bit) != 0 ? Ready[i] : Dlo[i];
            long earliest = Math.Max(Plo[i], delivered+Use[i]);
            long arrive = Math.Max(clock+Travel[at,i], earliest);
            if (arrive > Phi[i]) continue;
            Path[depth] = N+i+1;
            Walk(i, arrive+Service[i], mask | pickup, remaining, chosen,
                 weight+Pw[i], volume+Pv[i], length+Distance[at,i], depth+1);
        }
    }

    public static int Main(string[] args) {
        try {
            ProgressPath = args[2]; Limit = double.Parse(args[3], CultureInfo.InvariantCulture);
            using (BinaryReader r = new BinaryReader(File.OpenRead(args[0]))) {
                if (System.Text.Encoding.ASCII.GetString(r.ReadBytes(4)) != "M8TI") throw new Exception("Bad input magic");
                N=r.ReadInt32(); if (N<1 || N>20) throw new Exception("Expected 1..20 local orders");
                Start=r.ReadInt64(); End=r.ReadInt64(); Unload=r.ReadInt64(); Turnaround=r.ReadInt64();
                WeightCap=r.ReadDouble(); VolumeCap=r.ReadDouble();
                Travel=new long[N+1,N+1]; Distance=new double[N+1,N+1];
                for (int i=0;i<=N;i++) for (int j=0;j<=N;j++) { Travel[i,j]=r.ReadInt64(); Distance[i,j]=r.ReadDouble(); }
                Dlo=new long[N]; Dhi=new long[N]; Plo=new long[N]; Phi=new long[N];
                Service=new long[N]; Use=new long[N]; Ready=new long[N]; Path=new long[2*N];
                Dw=new double[N]; Dv=new double[N]; Pw=new double[N]; Pv=new double[N];
                for (int i=0;i<N;i++) {
                    Dlo[i]=r.ReadInt64(); Dhi[i]=r.ReadInt64(); Plo[i]=r.ReadInt64(); Phi[i]=r.ReadInt64();
                    Service[i]=r.ReadInt64(); Use[i]=r.ReadInt64();
                    Dw[i]=r.ReadDouble(); Dv[i]=r.ReadDouble(); Pw[i]=r.ReadDouble(); Pv[i]=r.ReadDouble();
                    Ready[i]=-1;
                }
            }
            Clock=Stopwatch.StartNew();
            for (long chosen=0; chosen<(1L<<N); chosen++) {
                if (Clock.Elapsed.TotalSeconds >= Limit) { Expired=true; break; }
                double weight=0, volume=0;
                for (int i=0;i<N;i++) if ((chosen & (1L<<i)) != 0) { weight+=Dw[i]; volume+=Dv[i]; }
                if (weight>WeightCap+1e-8 || volume>VolumeCap+1e-8) continue;
                Walk(N, Start, 0, chosen, chosen, weight, volume, 0.0, 0);
                if (Expired) break;
            }
            Report();
            using (BinaryWriter w=new BinaryWriter(File.Create(args[1]))) {
                w.Write(System.Text.Encoding.ASCII.GetBytes("M8TO"));
                w.Write(Expired ? 0 : 1); w.Write(Prefixes); w.Write(Clock.Elapsed.TotalSeconds); w.Write(Columns.Count);
                foreach (KeyValuePair<long,Entry> pair in Columns) {
                    Entry e=pair.Value; w.Write(pair.Key); w.Write(e.Distance); w.Write(e.Count);
                    w.Write(e.A); w.Write(e.B); w.Write(e.C); w.Write(e.D);
                }
            }
            return 0;
        } catch (Exception e) { Console.Error.WriteLine(e.ToString()); return 1; }
    }
}
