import numbers
import bisect
import math
from warnings import warn

import h5py
import numpy as np

from .stepresult import StepResult, VERSION_RESULTS
import openmc.checkvalue as cv
from openmc.data.library import DataLibrary
from openmc.material import Material, Materials
from openmc.exceptions import DataError

__all__ = ["Results", "ResultsList"]


def _get_time_as(seconds, units):
    if units == "d":
        return seconds / (60 * 60 * 24)
    elif units == "h":
        return seconds / (60 * 60)
    elif units == "min":
        return seconds / 60
    else:
        return seconds


class Results(list):
    """Results from a depletion simulation

    The :class:`Results` class acts as a list that stores the results from
    each depletion step and provides extra methods for interrogating these
    results.

    Parameters
    ----------
    filename : str
        Path to depletion result file

    """
    def __init__(self, filename=None):
        data = []
        if filename is not None:
            with h5py.File(str(filename), "r") as fh:
                cv.check_filetype_version(fh, 'depletion results', VERSION_RESULTS[0])

                # Get number of results stored
                n = fh["number"][...].shape[0]

                for i in range(n):
                    data.append(StepResult.from_hdf5(fh, i))
        super().__init__(data)


    @classmethod
    def from_hdf5(cls, filename):
        """Load in depletion results from a previous file

        Parameters
        ----------
        filename : str
            Path to depletion result file

        Returns
        -------
        Results
            New instance of depletion results

        """
        warn(
            "The ResultsList.from_hdf5(...) method is no longer necessary and will "
            "be removed in a future version of OpenMC. Use Results(...) instead.",
            FutureWarning
        )
        return cls(filename)

    def get_atoms(self, mat, nuc, nuc_units="atoms", time_units="s"):
        """Get number of nuclides over time from a single material

        .. note::
            Initial values for some isotopes that do not appear in
            initial concentrations may be non-zero, depending on the
            value of the :attr:`openmc.deplete.Operator.dilute_initial`
            attribute. The :class:`openmc.deplete.Operator` class adds isotopes
            according to this setting, which can be set to zero.

        Parameters
        ----------
        mat : openmc.Material, str
            Material object or material id to evaluate
        nuc : str
            Nuclide name to evaluate
        nuc_units : {"atoms", "atom/b-cm", "atom/cm3"}, optional
            Units for the returned concentration. Default is ``"atoms"``

            .. versionadded:: 0.12
        time_units : {"s", "min", "h", "d"}, optional
            Units for the returned time array. Default is ``"s"`` to
            return the value in seconds.

            .. versionadded:: 0.12

        Returns
        -------
        times : numpy.ndarray
            Array of times in units of ``time_units``
        concentrations : numpy.ndarray
            Concentration of specified nuclide in units of ``nuc_units``

        """
        cv.check_value("time_units", time_units, {"s", "d", "min", "h"})
        cv.check_value("nuc_units", nuc_units,
                    {"atoms", "atom/b-cm", "atom/cm3"})

        if isinstance(mat, Material):
            mat_id = str(mat.id)
        elif isinstance(mat, str):
            mat_id = mat
        else:
            raise TypeError('mat should be of type openmc.Material or str')
        times = np.empty_like(self, dtype=float)
        concentrations = np.empty_like(self, dtype=float)

        # Evaluate value in each region
        for i, result in enumerate(self):
            times[i] = result.time[0]
            concentrations[i] = result[0, mat_id, nuc]

        # Unit conversions
        times = _get_time_as(times, time_units)
        if nuc_units != "atoms":
            # Divide by volume to get density
            concentrations /= self[0].volume[mat_id]
            if nuc_units == "atom/b-cm":
                # 1 barn = 1e-24 cm^2
                concentrations *= 1e-24

        return times, concentrations

    def get_reaction_rate(self, mat, nuc, rx):
        """Get reaction rate in a single material/nuclide over time

        .. note::

            Initial values for some isotopes that do not appear in
            initial concentrations may be non-zero, depending on the
            value of :class:`openmc.deplete.Operator` ``dilute_initial``
            The :class:`openmc.deplete.Operator` adds isotopes according
            to this setting, which can be set to zero.

        Parameters
        ----------
        mat : openmc.Material, str
            Material object or material id to evaluate
        nuc : str
            Nuclide name to evaluate
        rx : str
            Reaction rate to evaluate

        Returns
        -------
        times : numpy.ndarray
            Array of times in [s]
        rates : numpy.ndarray
            Array of reaction rates

        """
        times = np.empty_like(self, dtype=float)
        rates = np.empty_like(self, dtype=float)

        if isinstance(mat, Material):
            mat_id = str(mat.id)
        elif isinstance(mat, str):
            mat_id = mat
        else:
            raise TypeError('mat should be of type openmc.Material or str')

        # Evaluate value in each region
        for i, result in enumerate(self):
            times[i] = result.time[0]
            rates[i] = result.rates[0].get(mat_id, nuc, rx) * result[0, mat, nuc]

        return times, rates

    def get_keff(self, time_units='s'):
        """Evaluates the eigenvalue from a results list.

        .. versionadded:: 0.13.1

        Parameters
        ----------
        time_units : {"s", "d", "h", "min"}, optional
            Desired units for the times array

        Returns
        -------
        times : numpy.ndarray
            Array of times in specified units
        eigenvalues : numpy.ndarray
            k-eigenvalue at each time. Column 0
            contains the eigenvalue, while column
            1 contains the associated uncertainty

        """
        cv.check_value("time_units", time_units, {"s", "d", "min", "h"})

        times = np.empty_like(self, dtype=float)
        eigenvalues = np.empty((len(self), 2), dtype=float)

        # Get time/eigenvalue at each point
        for i, result in enumerate(self):
            times[i] = result.time[0]
            eigenvalues[i] = result.k[0]

        # Convert time units if necessary
        times = _get_time_as(times, time_units)
        return times, eigenvalues

    def get_eigenvalue(self, time_units='s'):
        warn("The get_eigenvalue(...) function has been renamed get_keff and "
             "will be removed in a future version of OpenMC.", FutureWarning)
        return self.get_keff(time_units)

    def get_depletion_time(self):
        """Return an array of the average time to deplete a material

        .. note::
            The return value will have one fewer values than several other
            methods, such as :meth:`get_keff`, because no depletion is performed
            at the final transport stage.

        Returns
        -------
        times : numpy.ndarray
            Vector of average time to deplete a single material
            across all processes and materials.

        """
        times = np.empty(len(self) - 1)
        # Need special logic because the predictor
        # writes EOS values for step i as BOS values
        # for step i+1
        # The first proc_time may be zero
        if self[0].proc_time > 0.0:
            items = self[:-1]
        else:
            items = self[1:]
        for ix, res in enumerate(items):
            times[ix] = res.proc_time
        return times

    def get_times(self, time_units="d") -> np.ndarray:
        """Return the points in time that define the depletion schedule

        .. versionadded:: 0.12.1

        Parameters
        ----------
        time_units : {"s", "d", "h", "min"}, optional
            Return the vector in these units. Default is to
            convert to days

        Returns
        -------
        numpy.ndarray
            1-D vector of time points

        """
        cv.check_value("time_units", time_units, {"s", "d", "min", "h"})

        times = np.fromiter(
            (r.time[0] for r in self),
            dtype=self[0].time.dtype,
            count=len(self),
        )

        return _get_time_as(times, time_units)

    def get_step_where(
        self, time, time_units="d", atol=1e-6, rtol=1e-3
    ) -> int:
        """Return the index closest to a given point in time

        In the event ``time`` lies exactly between two points, the
        lower index will be returned. It is possible that the index
        will be at most one past the point in time requested, but only
        according to tolerances requested.

        Passing ``atol=math.inf`` and ``rtol=math.inf`` will return
        the closest index to the requested point.

        .. versionadded:: 0.12.1

        Parameters
        ----------
        time : float
            Desired point in time
        time_units : {"s", "d", "min", "h"}, optional
            Units on ``time``. Default: days
        atol : float, optional
            Absolute tolerance (in ``time_units``) if ``time`` is not
            found.
        rtol : float, optional
            Relative tolerance if ``time`` is not found.

        Returns
        -------
        int

        """
        cv.check_type("time", time, numbers.Real)
        cv.check_type("atol", atol, numbers.Real)
        cv.check_type("rtol", rtol, numbers.Real)

        times = self.get_times(time_units)

        if times[0] < time < times[-1]:
            ix = bisect.bisect_left(times, time)
            if ix == times.size:
                ix -= 1
            # Bisection will place us either directly on the point
            # or one-past the first value less than time
            elif time - times[ix - 1] <= times[ix] - time:
                ix -= 1
        elif times[0] >= time:
            ix = 0
        elif time >= times[-1]:
            ix = times.size - 1

        if math.isclose(time, times[ix], rel_tol=rtol, abs_tol=atol):
            return ix

        raise ValueError(
            "A value of {} {} was not found given absolute and "
            "relative tolerances {} and {}.".format(
                time, time_units, atol, rtol)
        )

    def export_to_materials(self, burnup_index, nuc_with_data=None) -> Materials:
        """Return openmc.Materials object based on results at a given step

        .. versionadded:: 0.12.1

        Parameters
        ----------
        burn_index : int
            Index of burnup step to evaluate. See also: get_step_where for
            obtaining burnup step indices from other data such as the time.
        nuc_with_data : Iterable of str, optional
            Nuclides to include in resulting materials.
            This can be specified if not all nuclides appearing in
            depletion results have associated neutron cross sections, and
            as such cannot be used in subsequent transport calculations.
            If not provided, nuclides from the cross_sections element of
            materials.xml will be used. If that element is not present,
            nuclides from OPENMC_CROSS_SECTIONS will be used.

        Returns
        -------
        mat_file : Materials
            A modified Materials instance containing depleted material data
            and original isotopic compositions of non-depletable materials
        """
        result = self[burnup_index]

        # Only materials found in the original materials.xml file will be
        # updated. If for some reason you have modified OpenMC to produce
        # new materials as depletion takes place, this method will not
        # work as expected and leave out that material.
        mat_file = Materials.from_xml("materials.xml")

        # Only nuclides with valid transport data will be written to
        # the new materials XML file. The precedence of nuclides to select
        # is first ones provided as a kwarg here, then ones specified
        # in the materials.xml file if provided, then finally from
        # the environment variable OPENMC_CROSS_SECTIONS.
        if nuc_with_data:
            cv.check_iterable_type('nuclide names', nuc_with_data, str)
            available_cross_sections = nuc_with_data
        else:
            # select cross_sections.xml file to use
            if mat_file.cross_sections:
                this_library = DataLibrary.from_xml(path=mat_file.cross_sections)
            else:
                this_library = DataLibrary.from_xml()

            # Find neutron libraries we have access to
            available_cross_sections = set()
            for lib in this_library.libraries:
                if lib['type'] == 'neutron':
                    available_cross_sections.update(lib['materials'])
            if not available_cross_sections:
                raise DataError('No neutron libraries found in cross_sections.xml')

        # Overwrite material definitions, if they can be found in the depletion
        # results, and save them to the new depleted xml file.
        for mat in mat_file:
            mat_id = str(mat.id)
            if mat_id in result.mat_to_ind:
                mat.volume = result.volume[mat_id]
                mat.set_density('sum')
                for nuc in result.nuc_to_ind:
                    if nuc not in available_cross_sections:
                        continue
                    atoms = result[0, mat_id, nuc]
                    if atoms > 0.0:
                        atoms_per_barn_cm = 1e-24 * atoms / mat.volume
                        mat.remove_nuclide(nuc) # Replace if it's there
                        mat.add_nuclide(nuc, atoms_per_barn_cm)

        return mat_file


# Retain deprecated name for the time being
ResultsList = Results
