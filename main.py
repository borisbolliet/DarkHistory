""" Module containing the main DarkHistory functions.

"""

import numpy as np
import pickle
from tqdm import tqdm_notebook as tqdm

from scipy.interpolate import interp1d

import darkhistory.physics as phys
import darkhistory.utilities as utils
import darkhistory.spec.spectools as spectools
import darkhistory.spec.transferfunclist as tflist
from darkhistory.spec.spectrum import Spectrum
from darkhistory.spec.spectra import Spectra
import darkhistory.history.tla as tla

from darkhistory.electrons.ics.ics_spectrum import ics_spec
from darkhistory.electrons.ics.ics_spectrum import nonrel_spec
from darkhistory.electrons.ics.ics_spectrum import rel_spec
from darkhistory.electrons.ics.ics_engloss_spectrum import engloss_spec
from darkhistory.electrons.ics.ics_cooling import get_ics_cooling_tf

from darkhistory.electrons import positronium as pos

from darkhistory.low_energy.lowE_deposition import compute_fs

def load_trans_funcs():
    # Load in the transferfunctions
    #!!! Should be a directory internal to DarkHistory
    print('Loading transfer functions...')
    user = 'gridgway'
    highengphot_tflist_arr = pickle.load(open("/Users/"+user+"/Dropbox (MIT)/Photon Deposition/tfunclist_photspec_60eV_complete_coarse.raw", "rb"))
    print('Loaded high energy photons...')
    lowengphot_tflist_arr  = pickle.load(open("/Users/"+user+"/Dropbox (MIT)/Photon Deposition/tfunclist_lowengphotspec_60eV_complete_coarse.raw", "rb"))
    print('Low energy photons...')
    lowengelec_tflist_arr  = pickle.load(open("/Users/"+user+"/Dropbox (MIT)/Photon Deposition/tfunclist_lowengelecspec_60eV_complete_coarse.raw", "rb"))
    print('Low energy electrons...')
    CMB_engloss_arr = pickle.load(open("/Users/"+user+"/Dropbox (MIT)/Photon Deposition/CMB_engloss_60eV_complete_coarse.raw", "rb"))
    print('CMB losses.\n')

    photeng = highengphot_tflist_arr[0].eng
    eleceng = lowengelec_tflist_arr[0].eng

    #Split photeng into high and low energy. 
    photeng_high = photeng[photeng > 60]
    photeng_low  = photeng[photeng <= 60]

    # Split eleceng into high and low energy. 
    eleceng_high = eleceng[eleceng > 3000]
    eleceng_low  = eleceng[eleceng <= 3000]


    print('Padding tflists with zeros...')
    for highengphot_tflist in highengphot_tflist_arr:
        for tf in highengphot_tflist:
            # Pad with zeros so that it becomes photeng x photeng. 
            tf._grid_vals = np.pad(tf.grid_vals, ((photeng_low.size, 0), (0, 0)), 'constant')
            tf._N_underflow = np.pad(tf._N_underflow, (photeng_low.size, 0), 'constant')
            tf._eng_underflow = np.pad(tf._eng_underflow, (photeng_low.size, 0), 'constant')
            tf._in_eng = photeng
            tf._eng = photeng
            tf._rs = tf.rs[0]*np.ones_like(photeng)

        highengphot_tflist._eng = photeng
        highengphot_tflist._in_eng = photeng
        highengphot_tflist._grid_vals = np.atleast_3d(
            np.stack([tf.grid_vals for tf in highengphot_tflist._tflist])
        )
    print("high energy photons...")

    # lowengphot_tflist.in_eng set to photeng_high
    for lowengphot_tflist in lowengphot_tflist_arr:
        for tf in lowengphot_tflist:
            # Pad with zeros so that it becomes photeng x photeng. 
            tf._grid_vals = np.pad(tf.grid_vals, ((photeng_low.size,0), (0,0)), 'constant')
            # Photons in the low energy bins should be immediately deposited.
            tf._grid_vals[0:photeng_low.size, 0:photeng_low.size] = np.identity(photeng_low.size)
            tf._N_underflow = np.pad(tf._N_underflow, (photeng_low.size, 0), 'constant')
            tf._eng_underflow = np.pad(tf._eng_underflow, (photeng_low.size, 0), 'constant')
            tf._in_eng = photeng
            tf._eng = photeng
            tf._rs = tf.rs[0]*np.ones_like(photeng)

        lowengphot_tflist._eng = photeng
        lowengphot_tflist._in_eng = photeng
        lowengphot_tflist._grid_vals = np.atleast_3d(
            np.stack([tf.grid_vals for tf in lowengphot_tflist._tflist])
        )
    print("low energy photons...")

    # lowengelec_tflist.in_eng set to photeng_high 
    for lowengelec_tflist in lowengelec_tflist_arr:
        for tf in lowengelec_tflist:
            # Pad with zeros so that it becomes photeng x eleceng. 
            tf._grid_vals = np.pad(tf.grid_vals, ((photeng_low.size,0), (0,0)), 'constant')
            tf._N_underflow = np.pad(tf._N_underflow, (photeng_low.size, 0), 'constant')
            tf._eng_underflow = np.pad(tf._eng_underflow, (photeng_low.size, 0), 'constant')
            tf._in_eng = photeng
            tf._eng = eleceng
            tf._rs = tf.rs[0]*np.ones_like(photeng)

        lowengelec_tflist._eng = eleceng
        lowengelec_tflist._in_eng = photeng
        lowengelec_tflist._grid_vals = np.atleast_3d(
            np.stack([tf.grid_vals for tf in lowengelec_tflist._tflist])
        )
    print("low energy electrons.\n")

    #!!! engloss must be included
    for engloss in CMB_engloss_arr:
        engloss = np.pad(engloss, ((0,0),(photeng_low.size, 0)), 'constant')

    # free electron fractions for which transfer functions are evaluated
    xes = 0.5 + 0.5*np.tanh([-5., -4.1, -3.2, -2.3, -1.4, -0.5, 0.4, 1.3, 2.2, 3.1, 4])

    print("Generating TransferFuncInterp objects for each tflist...")
    #interpolate at each of the electron fractions defined above
    highengphot_tf_interp = tflist.TransferFuncInterp(xes, highengphot_tflist_arr)
    lowengphot_tf_interp = tflist.TransferFuncInterp(xes, lowengphot_tflist_arr)
    lowengelec_tf_interp = tflist.TransferFuncInterp(xes, lowengelec_tflist_arr)
    print("Done.\n")

    return highengphot_tf_interp, lowengphot_tf_interp, lowengelec_tf_interp

def load_ics_data():
    """
        Load Inverse Compton Scattering Transfer Functions
    """
    Emax = 1e20
    Emin = 1e-8
    nEe = 5000
    nEp  = 5000

    dlnEp = np.log(Emax/Emin)/nEp
    lowengEp_rel = Emin*np.exp((np.arange(nEp)+0.5)*dlnEp)

    dlnEe = np.log(Emax/Emin)/nEe
    lowengEe_rel = Emin*np.exp((np.arange(nEe)+0.5)*dlnEe)

    Emax = 1e10
    Emin = 1e-8
    nEe = 5000
    nEp  = 5000

    dlnEp = np.log(Emax/Emin)/nEp
    lowengEp_nonrel = Emin*np.exp((np.arange(nEp)+0.5)*dlnEp)

    dlnEe = np.log(Emax/Emin)/nEe
    lowengEe_nonrel = Emin*np.exp((np.arange(nEe)+0.5)*dlnEe)

    print('********* Thomson regime scattered photon spectrum *********')
    ics_thomson_ref_tf = nonrel_spec(lowengEe_nonrel, lowengEp_nonrel, phys.TCMB(400))
    print('********* Relativistic regime scattered photon spectrum *********')
    ics_rel_ref_tf = rel_spec(lowengEe_rel, lowengEp_rel, phys.TCMB(400), inf_upp_bound=True)
    print('********* Thomson regime energy loss spectrum *********')
    engloss_ref_tf = engloss_spec(lowengEe_nonrel, lowengEp_nonrel, phys.TCMB(400), nonrel=True)

    return ics_thomson_ref_tf, ics_rel_ref_tf, engloss_ref_tf

def evolve(
    in_spec_elec, in_spec_phot,
    rate_func_N, rate_func_eng, end_rs,
    highengphot_tf_interp, lowengphot_tf_interp, lowengelec_tf_interp,
    ics_thomson_ref_tf, ics_rel_ref_tf, engloss_ref_tf,
    xe_init=None, Tm_init=None,
    coarsen_factor=1, std_soln=False, user=None
):
    """
    Main function that computes the temperature and ionization history.

    Parameters
    ----------
    in_spec_elec : Spectrum
        Spectrum per annihilation/decay into electrons. rs of this spectrum is the rs of the initial conditions.
    in_spec_phot : Spectrum
        Spectrum per annihilation/decay into photons.
    rate_func_N : function
        Function describing the rate of annihilation/decay, dN/(dV dt)
    rate_func_eng : function
        Function describing the rate of annihilation/decay, dE/(dV dt)
    xe_init : float
        xe at the initial redshift.
    Tm_init : float
        Matter temperature at the initial redshift.
    end_rs : float
        Final redshift to evolve to.
    coarsen_factor : int
        Coarsening to apply to the transfer function matrix.
    std_soln : bool
        If true, uses the standard TLA solution for f(z).
    user : str
        specify which user is accessing the code, so that the standard solution can be downloaded.  Must be changed!!!
    """

    # Initialize the next spectrum as None.
    next_highengphot_spec = None
    next_lowengphot_spec  = None
    next_lowengelec_spec  = None

    if (
        highengphot_tf_interp.dlnz    != lowengphot_tf_interp.dlnz
        or highengphot_tf_interp.dlnz != lowengelec_tf_interp.dlnz
        or lowengphot_tf_interp.dlnz  != lowengelec_tf_interp.dlnz
    ):
        raise TypeError('Input spectra must have the same rs.')

    if in_spec_elec.rs != in_spec_phot.rs:
        raise TypeError('Input spectra must have the same rs.')

    # redshift/timestep related quantities. 
    dlnz = highengphot_tf_interp.dlnz
    prev_rs = None
    rs = in_spec_phot.rs
    dt = dlnz/phys.hubble(rs)

    # Function that changes the normalization from per annihilation to per baryon. 
    def norm_fac(rs):
        return rate_func_N(rs) * dlnz * coarsen_factor /phys.hubble(rs) / (phys.nB * rs**3)

    elec_processes = False

    if in_spec_elec.totN() > 0:
        elec_processes = True

    if elec_processes:
        (ics_sec_phot_tf, ics_sec_elec_tf, continuum_loss) = get_ics_cooling_tf(
            ics_thomson_ref_tf, ics_rel_ref_tf, engloss_ref_tf,
            eleceng, photeng, rs, fast=True
        )

        ics_phot_spec = ics_sec_phot_tf.sum_specs(in_spec_elec)
        ics_lowengelec_spec = ics_sec_elec_tf.sum_specs(in_spec_elec)
        positronium_phot_spec = pos.weighted_photon_spec(photeng)*in_spec_elec.totN()/2
        if positronium_phot_spec.spec_type != 'N':
            positronium_phot_spec.switch_spec_type()
            init_inj_spec = (in_spec_phot + ics_phot_spec + positronium_phot_spec)*norm_fac(rs)
        else:
            init_inj_spec = in_spec_phot * norm_fac(rs)

    # Initialize the Spectra object that will contain all the 
    # output spectra during the evolution.
    out_highengphot_specs = Spectra([init_inj_spec], spec_type=init_inj_spec.spec_type)
    out_lowengphot_specs  = Spectra([], spec_type=init_inj_spec.spec_type)
    out_lowengelec_specs  = Spectra([], spec_type=init_inj_spec.spec_type)

    # Initialize the xe and T array that will store the solutions.
    xe_arr  = np.array([xe_init])
    Tm_arr = np.array([Tm_init])

    f_arr = np.array([])

    # Load the standard TLA solution if necessary.
    if std_soln:
        soln = np.loadtxt(open("/Users/"+user+"/Dropbox (MIT)/Photon Deposition/recfast_standard.txt", "rb"))
        xe_std  = interp1d(soln[:,0], soln[:,2])
        #Tm_std = interp1d(soln[0,:], soln[1,:])

    # Define these methods for speed.
    append_highengphot_spec = out_highengphot_specs.append
    append_lowengphot_spec  = out_lowengphot_specs.append
    append_lowengelec_spec  = out_lowengelec_specs.append

    # For the tqdm bar
    pbar = tqdm(total = np.floor((np.log(rs) - np.log(end_rs))/dlnz/coarsen_factor).astype(int))

    # Loop while we are still at a redshift above end_rs.
    while rs > end_rs:
        # Update tqdm bar.
        pbar.update(1)

        # If prev_rs exists, calculate xe and T_m. 
        if prev_rs is not None:
            # f_continuum, f_lyman, f_ionH, f_ionHe, f_heat
            # f_raw takes in dE/(dV dt)
            if std_soln:
                f_raw = compute_fs(
                    next_lowengelec_spec, next_lowengphot_spec,
                    np.array([1-xe_std(rs), 0, 0]), rate_func_eng(rs), dt
                )
            else:
                f_raw = compute_fs(
                    next_lowengelec_spec, next_lowengphot_spec,
                    np.array([1-xe_arr[-1], 0, 0]), rate_func_eng(rs), dt
                )

            f_arr = np.append(f_arr, f_raw)
            #print("rs, fs: ", rs, " ", f_raw)
            init_cond = np.array([Tm_arr[-1], xe_arr[-1], 0, 0])

            new_vals = tla.get_history(
                init_cond, f_raw[2], f_raw[1], f_raw[4],
                rate_func_eng, np.array([prev_rs, rs]),
                reion_switch = False
            )

            Tm_arr = np.append(Tm_arr, new_vals[-1,0])
            xe_arr  = np.append(xe_arr,  new_vals[-1,1])

        if std_soln:
            highengphot_tf = highengphot_tf_interp.get_tf(rs, xe_std(rs))
            lowengphot_tf  = lowengphot_tf_interp.get_tf(rs, xe_std(rs))
            lowengelec_tf  = lowengelec_tf_interp.get_tf(rs, xe_std(rs))
        else:
            highengphot_tf = highengphot_tf_interp.get_tf(rs, xe_arr[-1])
            lowengphot_tf  = lowengphot_tf_interp.get_tf(rs, xe_arr[-1])
            lowengelec_tf  = lowengelec_tf_interp.get_tf(rs, xe_arr[-1])


        if coarsen_factor > 1:
            prop_tf = np.zeros_like(highengphot_tf._grid_vals)
            for i in np.arange(coarsen_factor):
                prop_tf += matrix_power(highengphot_tf._grid_vals, i)
            lowengphot_tf._grid_vals = np.matmul(prop_tf, lowengphot_tf._grid_vals)
            lowengelec_tf._grid_vals = np.matmul(prop_tf, lowengelec_tf._grid_vals)
            highengphot_tf._grid_vals = matrix_power(
                highengphot_tf._grid_vals, coarsen_factor
            )

        next_highengphot_spec = highengphot_tf.sum_specs(out_highengphot_specs[-1])
        next_lowengphot_spec  = lowengphot_tf.sum_specs(out_highengphot_specs[-1])

        if elec_processes:
            next_lowengelec_spec  = lowengelec_tf.sum_specs(out_highengphot_specs[-1]) + ics_lowengelec_spec*norm_fac(rs)
        else:
            next_lowengelec_spec  = lowengelec_tf.sum_specs(out_highengphot_specs[-1])

        # Re-define existing variables.
        prev_rs = rs
        rs = np.exp(np.log(rs) - 0.002)

        dt = dlnz/phys.hubble(rs)
        next_highengphot_spec.rs = rs
        next_lowengphot_spec.rs  = rs
        next_lowengelec_spec.rs  = rs

        # Add the next injection spectrum to next_highengphot_spec
        if elec_processes:
            (ics_sec_phot_tf, ics_sec_elec_tf, continuum_loss) = get_ics_cooling_tf(
                ics_thomson_ref_tf_2, ics_rel_ref_tf_2, engloss_ref_tf_2,
                eleceng, photeng, rs, fast=True
            )

            ics_phot_spec = ics_sec_phot_tf.sum_specs(in_spec_elec)
            ics_lowengelec_spec = ics_sec_elec_tf.sum_specs(in_spec_elec)

            next_inj_spec = (in_spec_phot + ics_phot_spec + positronium_phot_spec)*norm_fac(rs)
        else:
            next_inj_spec = in_spec_phot * norm_fac(rs)

        # This keeps the redshift. 
        next_highengphot_spec.N += next_inj_spec.N

        append_highengphot_spec(next_highengphot_spec)
        append_lowengphot_spec(next_lowengphot_spec)
        append_lowengelec_spec(next_lowengelec_spec)

    f_arr = np.reshape(f_arr,(int(len(f_arr)/5), 5))
    return (
        xe_arr, Tm_arr,
        out_highengphot_specs, out_lowengphot_specs, out_lowengelec_specs, f_arr
    )
