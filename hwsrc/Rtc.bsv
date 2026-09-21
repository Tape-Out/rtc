package Rtc;

import Vector::*;
import RegIf::*;
import RtcRegs::*;

// 本包不认识任何总线：对外只给中立的 RegIf，接哪种总线由 wrap 或装配决定。
typedef struct {
  Bool alarm;
} RtcCfg;

interface RtcIfc#(numeric type aw, numeric type dw, numeric type alarms);
  interface RegIf#(aw, dw) regs;
  (* always_ready *) method Bool irq;
endinterface

module mkRtc#(RtcCfg cfg)(RtcIfc#(aw, dw, alarms))
    provisos (Mul#(TDiv#(dw, 8), 8, dw), Add#(_a, 8, aw), Add#(_b, 32, dw),
              Add#(_c, 4, dw), Add#(_d, 1, dw),
              // 写的是哪一个闹钟，下标要塞得进寄存器组给的宽度
              Add#(_e, TLog#(TAdd#(alarms, 1)), 8));

  RtcRegsIfc#(aw, dw, alarms) r <- mkRtcRegs(RtcRegsCfg { alarm: cfg.alarm });

  // 分频靠比较位，不做除法：scale 是 2 的幂次，直接看低几位跑满没有
  Reg#(Bit#(16)) pre <- mkReg(0);

  rule tick (r.cfg_en == 1);
    Bit#(16) top = (16'h1 << r.cfg_scale) - 1;
    if (pre >= top) begin
      pre <= 0;
      r.counter_in(r.counter + 1);
    end else
      pre <= pre + 1;
  endrule

  // 比较是「大于等于」而不是「正等于」（手册 16.3：rtcs >= rtccmp）。等于比较有个
  // 藏得很深的后果：**设在过去的闹钟永远不响**，而「读计数、加个差值、写回去」正是
  // 最常见的用法——差值算小了或者写晚了，那一次闹钟就整整丢掉一圈。
  //
  // 但纯电平比较又有两个老问题：两者复位都是 0，使能那一刻没配过的闹钟会当场全响；
  // 计数器在一个值上停留整个分频周期，写一清零刚清掉就被硬件重新置上。
  // 所以取边沿——「这一拍过了、上一拍还没过」才置位，并且：
  //   · 上一拍的状态复位成「已经过了」，使能那一刻就不会全响
  //   · 软件写了新的比较值就强制记成「还没过」，于是设在过去的闹钟下一拍当场响
  // 写进来的新值下一拍才生效，所以清「已经过了」与判命中差一拍，正好对上。
  Reg#(Bit#(4)) prevGe <- mkReg(4'hF);

  // 写脉冲要先记一拍再用。直接读它，fire 就既要排在总线方法之后（脉冲是方法发的）
  // 又要排在它之前（ista 的 CReg 口 0 归规则），两边对不上，bsc 随手定一个次序，
  // 表现是脉冲永远读不到。记一拍还顺带对上了另一件事：写进来的新比较值本来也是
  // 下一拍才生效。
  Reg#(Bool)                            wrPend <- mkReg(False);
  Reg#(Bit#(TLog#(TAdd#(alarms, 1))))   wrIdx  <- mkReg(0);

  rule mark;
    wrPend <= r.alarm_wr;
    wrIdx  <= r.alarm_wr_i;
  endrule

  if (cfg.alarm) begin
    rule fire (r.cfg_en == 1);
      Bit#(4) ge = 0;
      Bit#(4) hit = 0;
      for (Integer i = 0; i < valueOf(alarms); i = i + 1) begin
        Bool now  = r.counter >= r.alarm[i];
        Bool mine = wrPend && wrIdx == fromInteger(i);
        ge[i] = pack(now && !mine);
        if (now && prevGe[i] == 0) hit[i] = 1;
      end
      prevGe <= ge;
      r.ista_set(hit);
    endrule
  end

  interface regs = r.regs;
  method Bool irq = cfg.alarm && r.ista != 0;
endmodule

endpackage
