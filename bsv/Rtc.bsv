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
              Add#(_c, 4, dw), Add#(_d, 1, dw));

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

  if (cfg.alarm) begin
    rule fire (r.cfg_en == 1);
      Bit#(4) hit = 0;
      for (Integer i = 0; i < valueOf(alarms); i = i + 1)
        if (r.counter == r.alarm[i]) hit[i] = 1;
      r.ista_set(hit);
    endrule
  end

  interface regs = r.regs;
  method Bool irq = cfg.alarm && r.ista != 0;
endmodule

endpackage
