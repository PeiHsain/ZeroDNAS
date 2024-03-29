import math
import torch.nn as nn
import re
from copy import deepcopy
from lib.models.blocks.yolo_blocks import *
from lib.models.blocks.yolo_blocks_search import *
from lib.utils.general import random_testing

from timm.utils import *
from timm.models.layers.activations import Swish
from timm.models.layers import CondConv2d, get_condconv_initializer


def parse_model(d, ch):  # model_dict, input_channels(3)
    print('\n%3s%18s%3s%10s  %-40s%-30s' % ('', 'from', 'n', 'params', 'module', 'arguments'))
    anchors, nc, gd, gw = d['anchors'], d['nc'], d['depth_multiple'], d['width_multiple']
    na = (len(anchors[0]) // 2) if isinstance(anchors, list) else anchors  # number of anchors
    no = na * (nc + 5)  # number of outputs = anchors * (classes + 5)
    # csp_gammas = d['csp_gammas']
    # csp_bottle_count = 0
    # gamma = d['csp_gammas']
    # it = iter(gamma)
    
    stages = nn.ModuleList()
    layers, save, c2 = [], [], ch[-1]  # layers, savelist, ch out
    for i, (f, n, m, args) in enumerate(d['backbone'] + d['head']):  # from, number, module, args
        # blocks = nn.ModuleList()
        # for j, (f, n, m, args) in enumerate(blocks_args):
            #################################
            # Common Args PreProcessing
            #################################
            for j, a in enumerate(args):
                try:
                    args[j] = eval(a) if isinstance(a, str) else a  # eval strings
                except:
                    pass
            n = max(round(n * gd), 1) if n > 1 else n  # depth gain
            m = eval(m) if isinstance(m, str) else m  # eval strings
            block_args = {}
            #################################
            # Specific Args PreProcessing
            #################################
            if m in [nn.Conv2d, Conv, Bottleneck, SPP, DWConv, MixConv2d, Focus, CrossConv, BottleneckCSP, BottleneckCSP2, SPPCSP, VoVCSP, C3, \
                RepConv, ELAN, ELAN2, SPPCSPC]:
                
                c1, c2 = ch[f], args[0]
                

                block_args['stride']  = 1 if len(args) < 3 else args[2]
                

                # Normal
                # if i > 0 and args[0] != no:  # channel expansion factor
                #     ex = 1.75  # exponential (default 2.0)
                #     e = math.log(c2 / ch[1]) / math.log(2)
                #     c2 = int(ch[1] * ex ** e)
                # if m != Focus:

                # c2 = make_divisible(c2 * gw, 8) if c2 != no else c2

                # Experimental
                # if i > 0 and args[0] != no:  # channel expansion factor
                #     ex = 1 + gw  # exponential (default 2.0)
                #     ch1 = 32  # ch[1]
                #     e = math.log(c2 / ch1) / math.log(2)  # level 1-n
                #     c2 = int(ch1 * ex ** e)
                # if m != Focus:
                #     c2 = make_divisible(c2, 8) if c2 != no else c2

                args = [c1, c2, *args[1:]]
                if m in [BottleneckCSP, BottleneckCSP2, SPPCSP, VoVCSP, C3]:
                    args.insert(2, n)
                    n = 1
                    if  m in [BottleneckCSP, BottleneckCSP2]:
                        try:
                            args.insert(3, next(it))    # [input_channel(3), output_channel(arg[0]) / no, number, gamma]
                        except StopIteration:
                            raise Exception("Number of gammas (%d) is not suitable for the architecture.", len(gamma))
            elif m in [HarDBlock, HarDBlock2]:
                c1 = ch[f]
                c2 = c1
                args = [c1, *args[:]]
            elif m is nn.BatchNorm2d:
                args = [ch[f]]
                c1 = c2 = ch[f]
            elif m is Concat:
                c1 = [ch[x] for x in f]
                c2 = sum(c1)
            elif m in [Detect, IDetect]:
                args.append([ch[x] for x in f])
                if isinstance(args[1], int):  # number of anchors
                    args[1] = [list(range(args[1] * 2))] * len(f)
            elif m is nn.Upsample or m is Upsample:
                c1 = ch[f]
                c2 = ch[f]
                block_args['scale_factor'] = 2
            ####################################################################
            # Searching Operation
            ####################################################################
            elif m in [BottleneckCSP_Search, BottleneckCSP2_Search]:
                c1, c2 = ch[f], args[0]
                if len(args) == 1:
                    gamma_space      = d['search_space'][m.__name__]['gamma_space']
                    bottleneck_space = d['search_space'][m.__name__]['bottleneck_space']
                    args = [c1, c2, gamma_space, bottleneck_space]
                else:
                    args = [c1, c2, *args[1:]]
                    
            elif m in [ELAN_Search, ELAN2_Search]:
                c1, c2, cn = ch[f], args[0], args[1]
                if len(args) == 2:
                    gamma_space      = d['search_space'][m.__name__]['gamma_space']
                    connection_space = d['search_space'][m.__name__]['connection_space']
                    args = [c1, c2, cn, connection_space, gamma_space]
                else:
                    args = [c1, c2, cn, *args[2:]]
                    
            elif m is Composite_Search:
                pass
                raise ValueError(f"Not Implement Block {str(Composite_Search)}")
            ####################################################################
            
            else:
                c2 = ch[f]
            
            block_args['in_chs']  = c1
            block_args['out_chs'] = c2
            block_args['f']       = f
            
            block = m(*args)
            block.block_arguments = block_args
            
            m_ = block
            t = str(m)[8:-2].replace('lib.models.blocks.', '')  # module type
            np = sum([x.numel() for x in m_.parameters()])  # number params
            m_.i, m_.f, m_.type, m_.np = i, f, t, np  # attach index, 'from' index, type, number params
            print('%3s%18s%3s%10.0f  %-40s%-30s' % (i, f, n, np, t, args))  # print
            save.extend(x % i for x in ([f] if isinstance(f, int) else f) if x != -1)  # append to savelist
            layers.append(m_)
            if i == 0: ch = []
            if m in [HarDBlock, HarDBlock2]:
                c2 = m_.get_out_ch()
                ch.append(c2)
            else:
                ch.append(c2)
            
            
            stages.append(block)
        
    return stages, sorted(save)



def parse_ksize(ss):
    if ss.isdigit():
        return int(ss)
    else:
        return [int(k) for k in ss.split('.')]


def decode_arch_def(
        arch_def,
        depth_multiplier=1.0,
        depth_trunc='ceil',
        experts_multiplier=1):
    arch_args = []
    for stack_idx, block_strings in enumerate(arch_def):
        assert isinstance(block_strings, list)
        
        stack_args = []
        repeats = []
        for block_str in block_strings:
            assert isinstance(block_str, str)
            ba, rep = decode_block_str(block_str)
            if ba.get('num_experts', 0) > 0 and experts_multiplier > 1:
                ba['num_experts'] *= experts_multiplier
            stack_args.append(ba)
            repeats.append(rep)
        arch_args.append(
            scale_stage_depth(
                stack_args,
                repeats,
                depth_multiplier,
                depth_trunc))
    return arch_args


def modify_block_args(block_args, n_bottlenecks, gamma=None, gamma_space=None):
    block_type = block_args['block_type']
    if block_type == 'bottlecsp':
        block_args['n_bottlenecks'] = n_bottlenecks #max number
        if gamma: block_args['gamma'] = gamma
        if gamma_space: block_args['gamma_space'] = gamma_space
    elif block_type == 'bottlecsp2':
        block_args['n_bottlenecks'] = n_bottlenecks #max number
        if gamma: block_args['gamma'] = gamma
        if gamma_space: block_args['gamma_space'] = gamma_space
    elif block_type == 'C3':
        block_args['n_bottlenecks'] = n_bottlenecks #max number
        if gamma: block_args['gamma'] = gamma
        if gamma_space: block_args['gamma_space'] = gamma_space
    # elif block_type == 'er':
    #     block_args['exp_kernel_size'] = kernel_size
    # else:
    #     block_args['dw_kernel_size'] = kernel_size

    # if block_type == 'ir' or block_type == 'er':
    #     block_args['exp_ratio'] = exp_ratio
    return block_args


def decode_block_str(block_str):
    """ Decode block definition string
    Gets a list of block arg (dicts) through a string notation of arguments.
    E.g. ir_r2_k3_s2_e1_i32_o16_se0.25_noskip
    All args can exist in any order with the exception of the leading string which
    is assumed to indicate the block type.
    leading string - block type (
      ir = InvertedResidual, ds = DepthwiseSep, dsa = DeptwhiseSep with pw act, cn = ConvBnAct)
    r - number of repeat blocks,
    k - kernel size,
    s - strides (1-9) or size of upsample,
    e - expansion ratio,
    c - output channels,
    se - squeeze/excitation ratio
    n - activation fn ('re', 'r6', 'hs', or 'sw')
    m - mode for upsample
    sf - scale factor for upsample
    Args:
        block_str: a string representation of block arguments.
    Returns:
        A list of block args (dicts)
    Raises:
        ValueError: if the string def not properly specified (TODO)
    """
    assert isinstance(block_str, str)
    ops = block_str.split('_')
    block_type = ops[0]  # take the block type off the front
    ops = ops[1:]
    options = {}
    noskip = False
    for op in ops:
        # string options being checked on individual basis, combine if they
        # grow
        if op == 'noskip':
            noskip = True
        elif op.startswith('num'):
            key = op[:3]
            value = op[3:]
            options[key] = value
        elif op.startswith('last'):
            key = op
            value = True
            options[key] = value
        elif op.startswith('np'):
            key = op
            value = True
            options[key] = value
        elif op.startswith('n'):
            # activation fn
            key = op[0]
            v = op[1:]
            if v == 're':
                value = nn.ReLU
            elif v == 'r6':
                value = nn.ReLU6
            elif v == 'sw':
                value = Swish
            else:
                continue
            options[key] = value
        elif op.startswith('m'):
            key = op[0]
            value = op[1:]
            options[key] = value
        else:
            # all numeric options
            splits = re.split(r'(\d.*)', op)
            if len(splits) >= 2:
                if splits[0] == 'f-':
                    key = 'f'
                    value = '-' + splits[1]
                    options[key] = value
                else:
                    key, value = splits[:2]
                    options[key] = value


    # if act_layer is None, the model default (passed to model init) will be
    # used
    act_layer = options['n'] if 'n' in options else None
    exp_kernel_size = parse_ksize(options['a']) if 'a' in options else 1
    pw_kernel_size = parse_ksize(options['p']) if 'p' in options else 1
    # FIXME hack to deal with in_chs issue in TPU def
    fake_in_chs = int(options['fc']) if 'fc' in options else 0
    num_repeat = int(options['r'])
    # each type of block has different valid arguments, fill accordingly
    if block_type == 'ir':
        block_args = dict(
            block_type=block_type,
            dw_kernel_size=parse_ksize(options['k']),
            exp_kernel_size=exp_kernel_size,
            pw_kernel_size=pw_kernel_size,
            out_chs=int(options['c']),
            exp_ratio=float(options['e']),
            se_ratio=float(options['se']) if 'se' in options else None,
            stride=int(options['s']),
            act_layer=act_layer,
            noskip=noskip,
        )
        if 'cc' in options:
            block_args['num_experts'] = int(options['cc'])
    elif block_type == 'ds' or block_type == 'dsa':
        block_args = dict(
            block_type=block_type,
            dw_kernel_size=parse_ksize(options['k']),
            pw_kernel_size=pw_kernel_size,
            out_chs=int(options['c']),
            se_ratio=float(options['se']) if 'se' in options else None,
            stride=int(options['s']),
            act_layer=act_layer,
            pw_act=block_type == 'dsa',
            noskip=block_type == 'dsa' or noskip,
        )
    elif block_type == 'cn':
        if 'f' in options.keys():
            if int(options['f']) < 0:
                options['f'] = int(options['f'])
            else:
                options['f'] = int(options['f']) - 1
            from_concat = options['f']
        block_args = dict(
            block_type=block_type,
            kernel_size=int(options['k']),
            out_chs=int(options['cout']),
            in_chs=int(options['cin']),
            stride=int(options['s']),
            last=options.get('last'),
            prunable=options.get('np'),
            from_concat=from_concat if 'f' in options.keys() else None,
            act_layer=act_layer,
        )
    elif block_type == 'bottle':
        block_args = dict(
            block_type=block_type,
            kernel_size=int(options['k']),
            out_chs=int(options['cout']),
            in_chs=int(options['cin']),
            stride=int(options['s']),
            act_layer=act_layer
        )
    elif block_type == 'bottlecsp':
        block_args = dict(
            block_type=block_type,
            kernel_size=int(options['k']),
            out_chs=int(options['cout']),
            in_chs=int(options['cin']),
            stride=int(options['s']),
            groups=int(options['g']) if 'g' in options.keys() else 1,
            n_bottlenecks=int(options['num']),
            act_layer=act_layer,
            gamma=float(options['gamma'])
        )
    elif block_type == 'c3':
        block_args = dict(
            block_type=block_type,
            kernel_size=int(options['k']),
            out_chs=int(options['cout']),
            in_chs=int(options['cin']),
            stride=int(options['s']),
            groups=int(options['g']) if 'g' in options.keys() else 1,
            n_bottlenecks=int(options['num']),
            act_layer=act_layer,
            gamma=float(options['gamma'])
        )
    elif block_type == 'bottlecsp2':
        block_args = dict(
            block_type=block_type,
            kernel_size=int(options['k']),
            out_chs=int(options['cout']),
            in_chs=int(options['cin']),
            stride=int(options['s']),
            groups=int(options['g']) if 'g' in options.keys() else 1,
            n_bottlenecks=int(options['num']),
            act_layer=act_layer,
        )
    elif block_type == 'sppcsp':
        block_args = dict(
            block_type=block_type,
            kernel_size=int(options['k']),
            out_chs=int(options['cout']),
            stride=int(options['s']),
            act_layer=act_layer,
            in_chs=int(options['cin'])
        )
    elif block_type == 'up':
        block_args = dict(
            block_type=block_type,
            size=int(options['s']) if 's' in options.keys() else None,
            scale_factor=int(options['sf']) if 'sf' in options.keys() else None,
            mode=options['m'] if 'm' in options.keys() else 'nearest',
            act_layer=act_layer,
            stride=1,
            out_chs=int(options['c'])
        )
    elif block_type == 'concat':
        if 'f' in options.keys():
            if int(options['f']) < 0:
                options['f'] = int(options['f'])
            else:
                options['f'] = int(options['f']) - 1
        else:
            options['f'] = None
        from_concat = options['f']
        block_args = dict(
            block_type=block_type,
            act_layer=act_layer,
            stride=1,
            out_chs=int(options['c']),
            from_concat=[-1, from_concat]
        )
    else:
        assert False, 'Unknown block type (%s)' % block_type

    return block_args, num_repeat


def scale_stage_depth(
        stack_args,
        repeats,
        depth_multiplier=1.0,
        depth_trunc='ceil'):
    """ Per-stage depth scaling
    Scales the block repeats in each stage. This depth scaling impl maintains
    compatibility with the EfficientNet scaling method, while allowing sensible
    scaling for other models that may have multiple block arg definitions in each stage.
    """

    # We scale the total repeat count for each stage, there may be multiple
    # block arg defs per stage so we need to sum.
    num_repeat = sum(repeats)
    if depth_trunc == 'round':
        # Truncating to int by rounding allows stages with few repeats to remain
        # proportionally smaller for longer. This is a good choice when stage definitions
        # include single repeat stages that we'd prefer to keep that way as
        # long as possible
        num_repeat_scaled = max(1, round(num_repeat * depth_multiplier))
    else:
        # The default for EfficientNet truncates repeats to int via 'ceil'.
        # Any multiplier > 1.0 will result in an increased depth for every
        # stage.
        num_repeat_scaled = int(math.ceil(num_repeat * depth_multiplier))

    # Proportionally distribute repeat count scaling to each block definition in the stage.
    # Allocation is done in reverse as it results in the first block being less likely to be scaled.
    # The first block makes less sense to repeat in most of the arch
    # definitions.
    repeats_scaled = []
    for r in repeats[::-1]:
        rs = max(1, round((r / num_repeat * num_repeat_scaled)))
        repeats_scaled.append(rs)
        num_repeat -= r
        num_repeat_scaled -= rs
    repeats_scaled = repeats_scaled[::-1]

    # Apply the calculated scaling to each block arg in the stage
    sa_scaled = []
    for ba, rep in zip(stack_args, repeats_scaled):
        sa_scaled.extend([deepcopy(ba) for _ in range(rep)])
    return sa_scaled


def init_weight_goog(m, n='', fix_group_fanout=True, last_bn=None):
    """ Weight initialization as per Tensorflow official implementations.
    Args:
        m (nn.Module): module to init
        n (str): module name
        fix_group_fanout (bool): enable correct (matching Tensorflow TPU impl) fanout calculation w/ group convs
    Handles layers in EfficientNet, EfficientNet-CondConv, MixNet, MnasNet, MobileNetV3, etc:
    * https://github.com/tensorflow/tpu/blob/master/models/official/mnasnet/mnasnet_model.py
    * https://github.com/tensorflow/tpu/blob/master/models/official/efficientnet/efficientnet_model.py
    """
    if isinstance(m, CondConv2d):
        fan_out = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
        if fix_group_fanout:
            fan_out //= m.groups
        init_weight_fn = get_condconv_initializer(lambda w: w.data.normal_(
            0, math.sqrt(2.0 / fan_out)), m.num_experts, m.weight_shape)
        init_weight_fn(m.weight)
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.Conv2d):
        fan_out = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
        if fix_group_fanout:
            fan_out //= m.groups
        m.weight.data.normal_(0, math.sqrt(2.0 / fan_out))
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.BatchNorm2d):
        if n in last_bn:
            m.weight.data.zero_()
            m.bias.data.zero_()
        else:
            m.weight.data.fill_(1.0)
            m.bias.data.zero_()
        m.weight.data.fill_(1.0)
        m.bias.data.zero_()
    elif isinstance(m, nn.Linear):
        fan_out = m.weight.size(0)  # fan-out
        fan_in = 0
        if 'routing_fn' in n:
            fan_in = m.weight.size(1)
        init_range = 1.0 / math.sqrt(fan_in + fan_out)
        m.weight.data.uniform_(-init_range, init_range)
        m.bias.data.zero_()


def efficientnet_init_weights(
        model: nn.Module,
        init_fn=None,
        zero_gamma=False):
    last_bn = []
    if zero_gamma:
        prev_n = ''
        for n, m in model.named_modules():
            if isinstance(m, nn.BatchNorm2d):
                if ''.join(
                    prev_n.split('.')[
                        :-
                        1]) != ''.join(
                    n.split('.')[
                        :-
                        1]):
                    last_bn.append(prev_n)
                prev_n = n
        last_bn.append(prev_n)

    init_fn = init_fn or init_weight_goog
    for n, m in model.named_modules():
        print(f'm={m} n={n}')
        init_fn(m, n, last_bn=last_bn)
        init_fn(m, n, last_bn=last_bn)
