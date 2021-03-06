import logging

from miasm2.jitter.jitload import Jitter, named_arguments
from miasm2.core.locationdb import LocationDB
from miasm2.core.utils import pck32, upck32
from miasm2.arch.mips32.sem import ir_mips32l, ir_mips32b
from miasm2.jitter.codegen import CGen
from miasm2.ir.ir import AssignBlock, IRBlock
import miasm2.expression.expression as m2_expr

log = logging.getLogger('jit_mips32')
hnd = logging.StreamHandler()
hnd.setFormatter(logging.Formatter("[%(levelname)s]: %(message)s"))
log.addHandler(hnd)
log.setLevel(logging.CRITICAL)


class mipsCGen(CGen):
    CODE_INIT = CGen.CODE_INIT + r"""
    unsigned int branch_dst_pc;
    unsigned int branch_dst_irdst;
    unsigned int branch_dst_set=0;
    """

    CODE_RETURN_NO_EXCEPTION = r"""
    %s:
    if (branch_dst_set) {
        %s = %s;
        BlockDst->address = %s;
    } else {
        BlockDst->address = %s;
    }
    return JIT_RET_NO_EXCEPTION;
    """

    def __init__(self, ir_arch):
        super(mipsCGen, self).__init__(ir_arch)
        self.delay_slot_dst = m2_expr.ExprId("branch_dst_irdst", 32)
        self.delay_slot_set = m2_expr.ExprId("branch_dst_set", 32)

    def block2assignblks(self, block):
        irblocks_list = super(mipsCGen, self).block2assignblks(block)
        for irblocks in irblocks_list:
            for blk_idx, irblock in enumerate(irblocks):
                has_breakflow = any(assignblock.instr.breakflow() for assignblock in irblock)
                if not has_breakflow:
                    continue

                irs = []
                for assignblock in irblock:
                    if self.ir_arch.pc not in assignblock:
                        irs.append(AssignBlock(assignments, assignblock.instr))
                        continue
                    assignments = dict(assignblock)
                    # Add internal branch destination
                    assignments[self.delay_slot_dst] = assignblock[
                        self.ir_arch.pc]
                    assignments[self.delay_slot_set] = m2_expr.ExprInt(1, 32)
                    # Replace IRDst with next instruction
                    dst_loc_key = self.ir_arch.get_next_instr(assignblock.instr)
                    assignments[self.ir_arch.IRDst] = m2_expr.ExprLoc(dst_loc_key, 32)
                    irs.append(AssignBlock(assignments, assignblock.instr))
                irblocks[blk_idx] = IRBlock(irblock.loc_key, irs)

        return irblocks_list

    def gen_finalize(self, block):
        """
        Generate the C code for the final block instruction
        """

        loc_key = self.get_block_post_label(block)
        offset = self.ir_arch.loc_db.get_location_offset(loc_key)
        out = (self.CODE_RETURN_NO_EXCEPTION % (loc_key,
                                                self.C_PC,
                                                m2_expr.ExprId('branch_dst_irdst', 32),
                                                m2_expr.ExprId('branch_dst_irdst', 32),
                                                self.id_to_c(m2_expr.ExprInt(offset, 32)))
              ).split('\n')
        return out


class jitter_mips32l(Jitter):

    C_Gen = mipsCGen

    def __init__(self, *args, **kwargs):
        sp = LocationDB()
        Jitter.__init__(self, ir_mips32l(sp), *args, **kwargs)
        self.vm.set_little_endian()

    def push_uint32_t(self, value):
        self.cpu.SP -= 4
        self.vm.set_mem(self.cpu.SP, pck32(value))

    def pop_uint32_t(self):
        value = upck32(self.vm.get_mem(self.cpu.SP, 4))
        self.cpu.SP += 4
        return value

    def get_stack_arg(self, index):
        return upck32(self.vm.get_mem(self.cpu.SP + 4 * index, 4))

    def init_run(self, *args, **kwargs):
        Jitter.init_run(self, *args, **kwargs)
        self.cpu.PC = self.pc

    # calling conventions

    @named_arguments
    def func_args_stdcall(self, n_args):
        args = [self.get_arg_n_stdcall(i) for i in xrange(n_args)]
        ret_ad = self.cpu.RA
        return ret_ad, args

    def func_ret_stdcall(self, ret_addr, ret_value1=None, ret_value2=None):
        self.pc = self.cpu.PC = ret_addr
        if ret_value1 is not None:
            self.cpu.V0 = ret_value1
        if ret_value2 is not None:
            self.cpu.V1 = ret_value2
        return True

    def func_prepare_stdcall(self, ret_addr, *args):
        for index in xrange(min(len(args), 4)):
            setattr(self.cpu, 'A%d' % index, args[index])
        for index in xrange(4, len(args)):
            self.vm.set_mem(self.cpu.SP + 4 * (index - 4), pck32(args[index]))
        self.cpu.RA = ret_addr

    def get_arg_n_stdcall(self, index):
        if index < 4:
            arg = getattr(self.cpu, 'A%d' % index)
        else:
            arg = self.get_stack_arg(index-4)
        return arg


    func_args_systemv = func_args_stdcall
    func_ret_systemv = func_ret_stdcall
    func_prepare_systemv = func_prepare_stdcall
    get_arg_n_systemv = get_arg_n_stdcall


class jitter_mips32b(jitter_mips32l):

    def __init__(self, *args, **kwargs):
        sp = LocationDB()
        Jitter.__init__(self, ir_mips32b(sp), *args, **kwargs)
        self.vm.set_big_endian()
