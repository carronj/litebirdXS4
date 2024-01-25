import glob
import os
import healpy as hp
import numpy as np
from lenspyx.utils_hp import Alm, almxfl, gauss_beam
from lenspyx.remapping.utils_geom import Geom
from scipy.interpolate import UnivariateSpline as spl
from multiprocessing import cpu_count
from numpy.random import default_rng

# --- freqs on disk for alt1 chile wide ---
freqs = [30, 40, 90, 150, 220, 280]
XYs = ['II', 'IQ', 'IU', 'QQ', 'QU', 'UU']

fns = glob.glob(os.path.dirname(__file__) + '/inputs/alternative_1/chlat_cd_wide/*.fits')
if len(fns) == 2 * len(freqs): # Working locally
    print("Using files on " + os.path.dirname(__file__) + '/inputs/alternative_1/chlat_cd_wide')
    path2cov = os.path.dirname(__file__) + '/inputs/alternative_1/chlat_cd_wide/cov_%03d.fits'  # in K^2, nside 128
    path2hits = os.path.dirname(__file__) + '/inputs/alternative_1/chlat_cd_wide/hits_%03d.fits'
else: # Most likely we are on NERSC
    path2cov = '/global/cfs/cdirs/cmbs4/AoA/August2022/scaled_outputs/alternative_1/chlat_cd_wide/cov_%03d.fits' # in K^2, nside 128
    path2hits = '/global/cfs/cdirs/cmbs4/AoA/August2022/scaled_outputs/alternative_1/chlat_cd_wide/hits_%03d.fits'

nside = 128
tht_min, tht_max = (64 / 180 * np.pi, 155. / 180 * np.pi)

# --- PBDR 1/f noise parameters ---
freqs_PBDR = [25, 40, 90, 150, 230, 280] # (! not the same as DC0)
lknee_T_PBDR = [415, 391, 1932, 3017, 6740, 6792]
lknee_P_PBDR = [700] * len(freqs_PBDR)
a_T = [3.5] * len(freqs_PBDR)
a_P = [1.4] * len(freqs_PBDR)
nlev_T_PBDR = [27.1, 11.6, 2.0, 2.0, 6.9, 16.9]
nlev_P_PBDR = [37.6, 15.5, 2.7, 3.0, 9.8, 23.9]
beam_PBDR = [7.8, 5.3, 2.2, 1.4, 1.0, 0.9]

def get_lknee_a(TorP, freq):
    lknee, a = (lknee_T_PBDR, a_T) if TorP == 'T' else (lknee_P_PBDR, a_P)
    return spl(freqs_PBDR, lknee, k=1, s=0)(freq),spl(freqs_PBDR, a, k=1, s=0)(freq)

def get_nlev_ukamin(freq: int, xx: str, thres=10, nside_out=128):
    """Get's noise level from Cl_noise = mean (pix var map)


    """
    assert xx.upper() in ['II', 'QQ', 'UU']
    assert freq in freqs, (freq, freqs)
    path = path2cov%freq
    cov =  hp.read_map(path, field=XYs.index(xx.upper()))
    assert cov.size == 12 * nside ** 2, (cov.size, 12 * nside ** 2)
    if nside_out != nside:
        #FIXME: rather an map2alm and alm2map, to avoid features on the ig pixels boundaries ?
        cov = hp.ud_grade(cov, nside_out) * (nside_out / nside) ** 2
    cov = cov[np.where(cov > 0)]
    nlev = np.sqrt(np.mean(cov[np.where(cov < thres ** 2 * np.min(cov))]))
    nlev_uKamin = nlev * 1e6 * np.sqrt(hp.nside2pixarea(nside_out, degrees=True) * 60 * 60)
    return nlev_uKamin

def get_rhits(freq: int, nside_out: int):
    assert freq in freqs
    hits =  hp.read_map(path2hits%freq)
    return hp.ud_grade(hits / np.max(hits), nside_out)


def get_mask(xx:str, nside_mask=2048, freqs2consider=(90, 150), thres=10.):
    """Builds a mask including pixels observed in the CMB frequencies, with covariance not larger than thres ** 2 the minimal values

     """
    mask = np.ones(12 * nside_mask ** 2, dtype=bool)
    for freq in freqs2consider:
        cov = get_cov(freq, xx, nside_mask)
        covmin = np.min(cov[np.where(cov)])
        mask *= (cov > 0) & (cov <= thres ** 2 * covmin)
    return mask

def get_cov(freq: int, xx:str, nside_out=2048):
    assert xx.upper() in ['II', 'QQ', 'UU', 'QU']
    assert freq in freqs, (freq, freqs)
    path = path2cov%freq
    cov =  hp.read_map(path, field=XYs.index(xx.upper())) * 1e12 # in uK^2
    assert cov.size == 12 * nside ** 2, (cov.size, 12 * nside ** 2)
    return hp.ud_grade(cov, nside_out) * (nside_out / nside) ** 2

def _PBDRlike_noise_cls(freq, lmax=5120, _beamdeconvolved=True):
    lknee_I, alpha_I = get_lknee_a('T', freq)
    lknee_P, alpha_P = get_lknee_a('P', freq)
    cl_w = np.ones(lmax + 1, dtype=float)
    cl_knee_I = np.ones(lmax + 1, dtype=float)  * (np.arange(1, lmax + 2) / lknee_I) ** (-alpha_I)
    cl_knee_P = np.ones(lmax + 1, dtype=float)  * (np.arange(1, lmax + 2) / lknee_P) ** (-alpha_P)
    idf = freqs.index(freq)
    bl2 = gauss_beam(beam_PBDR[idf] / 180 / 60 * np.pi, lmax=lmax) ** 2 if _beamdeconvolved else 1.
    return  ((nlev_T_PBDR[idf] / 180 / 60 * np.pi) ** 2 * (cl_w + cl_knee_I) / bl2,
             (nlev_P_PBDR[idf] / 180 / 60 * np.pi) ** 2 * (cl_w + cl_knee_P) / bl2 * (np.arange(lmax + 1) > 1))

def _syn_alm(rng, cl:np.ndarray):
    lmax = cl.size - 1
    mmax = lmax
    rlm_dtype = np.float64

    alm_size = Alm.getsize(lmax, mmax)
    alm = 1j * rng.standard_normal(alm_size, dtype=rlm_dtype)
    alm += rng.standard_normal(alm_size, dtype=rlm_dtype)
    almxfl(alm, np.sqrt(cl * 0.5), mmax, True)
    real_idcs = Alm.getidx(lmax, np.arange(lmax + 1, dtype=int), 0)
    alm[real_idcs] = alm[real_idcs].real * np.sqrt(2.)
    return alm

def get_sim_map_v2(freq, lmax_atm=5120, nside_out=2048, seed=None, _facknee=1., _facwhite=1.):
    """
    
        Args:
            freq: CHLAT frequency
            lmax_atm: Gaussian atm. noise simulated down to this multipole
            nside_out: nside of the output map
            seed: input simulation seed

        Note:
            white noise perfecly independent from pixel to pixel
            Gaussian atm noise with equal E and B power, rescaled at the map level to match Reijo's depth at the lknee
            Include QU white noise covariance

    """
    # ---- sim params and geom ----
    mmax = lmax_atm
    lknee_I, alpha_I = get_lknee_a('T', freq)
    lknee_P, alpha_P = get_lknee_a('P', freq)

    cl_white = np.ones(lmax_atm + 1, dtype=float)
    s2_white_I = np.sum(cl_white * (2 * np.arange(lmax_atm + 1) + 1)) / (4 * np.pi)
    s2_white_P = np.sum(cl_white[2:] * (2 * np.arange(2, lmax_atm + 1) + 1)) / (4 * np.pi)  # ( EE + BB)

    s_II = np.sqrt(get_cov(freq, 'II', nside_out=nside_out))
    s_QQ = np.sqrt(get_cov(freq, 'QQ', nside_out=nside_out))
    s_UU = np.sqrt(get_cov(freq, 'UU', nside_out=nside_out))
    s2_QU = get_cov(freq, 'QU', nside_out=nside_out)
    # Ignoring IQ and IU cov
    rdet_P = s_QQ ** 2 * s_UU ** 2 - s2_QU ** 2
    nz = rdet_P.nonzero()


    geom = Geom.get_healpix_geometry(nside_out).restrict(tht_min, tht_max, False, update_ringstart=False)
    nthreads = int(os.environ.get('OMP_NUM_THREADS', cpu_count()))

    # ---- syn alm ----
    cl_knee_I = np.ones(lmax_atm + 1, dtype=float) * (np.arange(1, lmax_atm + 2) / lknee_I) ** (-alpha_I)
    cl_knee_P = np.ones(lmax_atm + 1, dtype=float) * (np.arange(1, lmax_atm + 2) / lknee_P) ** (-alpha_P)

    rng = default_rng(seed=seed)
    tlm = _syn_alm(rng, cl_knee_I * _facknee)
    eblm = np.zeros((2, Alm.getsize(lmax_atm, lmax_atm)), dtype=complex)
    eblm[0] = _syn_alm(rng, (cl_knee_P * _facknee) * (np.arange(lmax_atm + 1) >= 2))
    eblm[1] = _syn_alm(rng, (cl_knee_P * _facknee) * (np.arange(lmax_atm + 1) >= 2))

    # ---- maps ----
    tqumap = np.zeros((3, 12 * nside_out ** 2))
    geom.synthesis(tlm, 0, lmax=lmax_atm, mmax=mmax, nthreads=nthreads, map=tqumap[0:1])
    tqumap[0] *= (s_II / np.sqrt(s2_white_I))
    tqumap[0, nz] += s_II[nz] * rng.standard_normal(nz[0].size) # add perfectly white noise

    geom.synthesis(eblm, 2, lmax_atm, mmax, nthreads=nthreads, map=tqumap[1:])
    tqumap[1] *= (s_QQ  / np.sqrt(s2_white_P))
    tqumap[2] *= (s_UU  / np.sqrt(s2_white_P))
    # Include QU covariance in Pol
    xy = rng.standard_normal((2 , nz[0].size)) * _facwhite
    tqumap[1, nz] += s_QQ[nz] * xy[0]
    tqumap[2, nz] += (s2_QU[nz]  * xy[0] + np.sqrt(rdet_P[nz]) * xy[1]) /  s_QQ[nz]
    tqumap[np.where(tqumap == 0.)] = hp.UNSEEN
    return tqumap


def get_sim_map(freq, lmax=5120, nside_out=2048, seed=None, _facknee=1., _facwhite=1.):

    # ---- sim params and geom ----
    mmax = lmax
    lknee_I, alpha_I = get_lknee_a('T', freq)
    lknee_P, alpha_P = get_lknee_a('P', freq)

    cl_white = np.ones(lmax + 1, dtype=float)
    s2_white_I = np.sum(cl_white * (2 * np.arange(lmax + 1) + 1)) / (4 * np.pi)
    s2_white_P = np.sum(cl_white[2:] * (2 * np.arange(2, lmax + 1) + 1)) / (4 * np.pi) # ( EE + BB)

    rescal_I = np.sqrt(get_cov(freq, 'II', nside_out=nside_out) / s2_white_I) # Such that white part matches Reijo depth
    rescal_Q = np.sqrt(get_cov(freq, 'QQ', nside_out=nside_out) / s2_white_P)
    rescal_U = np.sqrt(get_cov(freq, 'UU', nside_out=nside_out) / s2_white_P)

    geom = Geom.get_healpix_geometry(nside_out).restrict(tht_min, tht_max, False, update_ringstart=False)
    nthreads = int(os.environ.get('OMP_NUM_THREADS', cpu_count()))

    # ---- syn alm ----
    cl_knee_I = np.ones(lmax + 1, dtype=float)  * (np.arange(1, lmax + 2) / lknee_I) ** (-alpha_I) * _facknee
    cl_knee_P = np.ones(lmax + 1, dtype=float)  * (np.arange(1, lmax + 2) / lknee_P) ** (-alpha_P) * _facknee

    rng = default_rng(seed=seed)
    tlm = _syn_alm(rng, cl_white * _facwhite + cl_knee_I * _facknee)
    eblm = np.zeros((2, Alm.getsize(lmax, lmax)), dtype=complex)
    eblm[0] = _syn_alm(rng, (cl_white * _facwhite + cl_knee_P * _facknee) * (np.arange(lmax + 1) >= 2))
    eblm[1] = _syn_alm(rng, (cl_white * _facwhite + cl_knee_P * _facknee) * (np.arange(lmax + 1) >= 2))

    # ---- maps ----
    tqumap = np.full((3, 12 * nside_out ** 2), hp.UNSEEN)
    geom.synthesis(tlm, 0, lmax=lmax, mmax=mmax, nthreads=nthreads, map=tqumap[0:1])
    tqumap[0, np.where(rescal_I)] *= rescal_I[np.where(rescal_I)]

    geom.synthesis(eblm, 2, lmax, mmax, nthreads=nthreads, map=tqumap[1:])
    tqumap[1, np.where(rescal_Q)] *= rescal_Q[np.where(rescal_Q)]
    tqumap[2, np.where(rescal_U)] *= rescal_U[np.where(rescal_U)]
    return tqumap

if __name__ == '__main__':
    for XX in ['II', 'QQ', 'UU']:
        for f in freqs:
            print(r'%s @ %03d GHz %.2f muK-amin'%(XX, f, get_nlev_ukamin(f, XX, thres=4)))
    print(r'lknee and alpha (I) :')
    for f in freqs:
        l, a = get_lknee_a('T', f)
        print(r' @ %03d GHz II  %03d  %.2f'%(f, l, a))
    print('rlknee and alpha (P) :')
    for f in freqs:
        l, a = get_lknee_a('P', f)
        print(r' @ %03d GHz P %03d  %.2f'%(f, l, a))
