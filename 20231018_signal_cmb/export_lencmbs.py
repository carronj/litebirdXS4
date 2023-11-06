"""Script exporting lensed CMBs down to lmax 4096 simulations for the litebirdxS4 project


"""
import argparse
import os, glob
import numpy as np
import time, sys
if os.curdir not in sys.path:
    sys.path.insert(0, os.curdir)
import lencmbs

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='cmb export script')
    parser.add_argument('-imin', dest='imin', default=0, type=int, help='starting index)')
    parser.add_argument('-imax', dest='imax', default=-1, type=int, help='last index')
    args = parser.parse_args()
    rank, size, barrier, finalize = 0, 1, lambda: -1, lambda: -1
    for idx in range(args.imin, args.imax + 1)[rank::size]:
        fn = os.path.join(os.environ['CFS'], 'cmbs4xlb/v1/cmb', 'lcdm_teb_%04d.npy'%idx)
        if not os.path.exists(fn) and (0 <= idx <= 499):
            t0 = time.time()
            t, eb = lencmbs.build_lensalms(idx, 4096, 0.)
            np.save(fn, np.array([t, eb[0], eb[1]]))
            if rank == 0:
                print(fn + ' done in %.1f sec'%(time.time() - t0))
        fn_klm = os.path.join(os.environ['CFS'], 'cmbs4xlb/v1/cmb', 'lcdm_k_%04d.npy'%idx)
        if not os.path.exists(fn_klm) and (0 <= idx <= 499):
            klm = lencmbs.build_lensalms(idx, 4096, 0., klm_only=True)
            np.save(fn_klm, klm)
    barrier()
    if rank == 0:
        fns = glob.glob(os.path.join(os.environ['CFS'], 'cmbs4xlb/v1/cmb',  'lcdm_teb_????.npy'))
        print('There are %s CMB arrays on disk'%len(fns))
    if rank == 0:
        fns = glob.glob(os.path.join(os.environ['CFS'], 'cmbs4xlb/v1/cmb',  'lcdm_k_????.npy'))
        print('There are %s klm arrays on disk'%len(fns))
    finalize()