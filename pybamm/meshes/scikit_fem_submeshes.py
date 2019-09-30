#
# scikit-fem meshes for use in PyBaMM
#
import pybamm

import skfem
import numpy as np


class ScikitSubMesh2D:
    """ Submesh class.
        Contains information about the 2D finite element mesh.
        Note: This class only allows for the use of piecewise-linear triangular
        finite elements.

        Parameters
        ----------
        lims : dict
            A dictionary that contains the limits of each
            spatial variable
        npts : dict
            A dictionary that contains the number of points to be used on each
            spatial variable
        tabs : dict
            A dictionary that contains information about the size and location of
            the tabs
        """

    def __init__(self, edges, coord_sys, tabs):
        self.edges = edges
        self.nodes = dict.fromkeys(["y", "z"])
        for var in self.nodes.keys():
            self.nodes[var] = (self.edges[var][1:] + self.edges[var][:-1]) / 2
        self.npts = len(self.edges["y"]) * len(self.edges["z"])
        self.coord_sys = coord_sys

        # create mesh
        self.fem_mesh = skfem.MeshTri.init_tensor(self.edges["y"], self.edges["z"])

        # get coordinates (returns a vector size 2*(Ny*Nz))
        self.coordinates = self.fem_mesh.p

        # create elements and basis
        self.element = skfem.ElementTriP1()
        self.basis = skfem.InteriorBasis(self.fem_mesh, self.element)
        self.facet_basis = skfem.FacetBasis(self.fem_mesh, self.element)

        # get degrees of freedom and facets which correspond to tabs, and
        # create facet basis for sub regions
        self.negative_tab_dofs = self.basis.get_dofs(
            lambda x: self.on_boundary(x[0], x[1], tabs["negative"])
        ).all()
        self.positive_tab_dofs = self.basis.get_dofs(
            lambda x: self.on_boundary(x[0], x[1], tabs["positive"])
        ).all()
        self.negative_tab_facets = self.fem_mesh.facets_satisfying(
            lambda x: self.on_boundary(x[0], x[1], tabs["negative"])
        )
        self.positive_tab_facets = self.fem_mesh.facets_satisfying(
            lambda x: self.on_boundary(x[0], x[1], tabs["positive"])
        )
        self.negative_tab_basis = skfem.FacetBasis(
            self.fem_mesh, self.element, facets=self.negative_tab_facets
        )
        self.positive_tab_basis = skfem.FacetBasis(
            self.fem_mesh, self.element, facets=self.positive_tab_facets
        )

    def on_boundary(self, y, z, tab):
        """
        A method to get the degrees of freedom corresponding to the subdomains
        for the tabs.
        """

        l_y = self.edges["y"][-1]
        l_z = self.edges["z"][-1]

        def near(x, point, tol=3e-16):
            return abs(x - point) < tol

        def between(x, interval, tol=3e-16):
            return x > interval[0] - tol and x < interval[1] + tol

        # Tab on top
        if near(tab["z_centre"], l_z):
            tab_left = tab["y_centre"] - tab["width"] / 2
            tab_right = tab["y_centre"] + tab["width"] / 2
            return [
                near(Z, l_z) and between(Y, [tab_left, tab_right]) for Y, Z in zip(y, z)
            ]
        # Tab on bottom
        elif near(tab["z_centre"], 0):
            tab_left = tab["y_centre"] - tab["width"] / 2
            tab_right = tab["y_centre"] + tab["width"] / 2
            return [
                near(Z, 0) and between(Y, [tab_left, tab_right]) for Y, Z in zip(y, z)
            ]
        # Tab on left
        elif near(tab["y_centre"], 0):
            tab_bottom = tab["z_centre"] - tab["width"] / 2
            tab_top = tab["z_centre"] + tab["width"] / 2
            return [
                near(Y, 0) and between(Z, [tab_bottom, tab_top]) for Y, Z in zip(y, z)
            ]
        # Tab on right
        elif near(tab["y_centre"], l_y):
            tab_bottom = tab["z_centre"] - tab["width"] / 2
            tab_top = tab["z_centre"] + tab["width"] / 2
            return [
                near(Y, l_y) and between(Z, [tab_bottom, tab_top]) for Y, Z in zip(y, z)
            ]
        else:
            raise pybamm.GeometryError("tab location not valid")


class ScikitUniform2DSubMesh(ScikitSubMesh2D):
    """
    Contains information about the 2D finite element mesh with uniform grid
    spacing (can be different spacing in y and z).
    Note: This class only allows for the use of piecewise-linear triangular
    finite elements.

    Parameters
    ----------
    lims : dict
        A dictionary that contains the limits of each
        spatial variable
    npts : dict
        A dictionary that contains the number of points to be used on each
        spatial variable
    tabs : dict
        A dictionary that contains information about the size and location of
        the tabs
    """

    def __init__(self, lims, npts, tabs):

        # check that two variables have been passed in
        if len(lims) != 2:
            raise pybamm.GeometryError(
                "lims should contain exactly two variables, not {}".format(len(lims))
            )

        # get spatial variables
        spatial_vars = list(lims.keys())

        # check coordinate system agrees
        if spatial_vars[0].coord_sys == spatial_vars[1].coord_sys:
            coord_sys = spatial_vars[0].coord_sys
        else:
            raise pybamm.DomainError(
                """spatial variables should have the same coordinate system,
                but have coordinate systems {} and {}""".format(
                    spatial_vars[0].coord_sys, spatial_vars[1].coord_sys
                )
            )

        # compute edges
        edges = {}
        for var in spatial_vars:
            if var.name not in ["y", "z"]:
                raise pybamm.DomainError(
                    "spatial variable must be y or z not {}".format(var.name)
                )
            else:
                edges[var.name] = np.linspace(
                    lims[var]["min"], lims[var]["max"], npts[var.id]
                )

        super().__init__(edges, coord_sys, tabs)


class ScikitTopExponential2DSubMesh(ScikitSubMesh2D):
    """
    Contains information about the 2D finite element mesh generated by taking the
    tensor product of a uniformly spaced grid in the y direction, and a unequally
    spaced grid in the z direction in which the points are clustered
    close to the top boundary using an exponential formula on the interval [a,b].
    The gridpoints in the z direction are given by

    .. math::
        z_{k} = (b-a) + \\frac{\\exp{-\\alpha k / N} - 1}{\\exp{-\\alpha} - 1}} + a,

    for k = 1, ..., N, where N is the number of nodes. Here alpha is
    a stretching factor. As the number of gridpoints tends to infinity, the ratio
    of the largest and smallest grid cells tends to exp(alpha).

    Parameters
    ----------
    lims : dict
        A dictionary that contains the limits of each
        spatial variable
    npts : dict
        A dictionary that contains the number of points to be used on each
        spatial variable
    tabs : dict
        A dictionary that contains information about the size and location of
        the tabs
    """

    def __init__(self, lims, npts, tabs):

        # check that two variables have been passed in
        if len(lims) != 2:
            raise pybamm.GeometryError(
                "lims should contain exactly two variables, not {}".format(len(lims))
            )

        # get spatial variables
        spatial_vars = list(lims.keys())

        # check coordinate system agrees
        if spatial_vars[0].coord_sys == spatial_vars[1].coord_sys:
            coord_sys = spatial_vars[0].coord_sys
        else:
            raise pybamm.DomainError(
                """spatial variables should have the same coordinate system,
                but have coordinate systems {} and {}""".format(
                    spatial_vars[0].coord_sys, spatial_vars[1].coord_sys
                )
            )

        # compute edges
        edges = {}
        for var in spatial_vars:
            if var.name not in ["y", "z"]:
                raise pybamm.DomainError(
                    "spatial variable must be y or z not {}".format(var.name)
                )
            elif var.name == "y":
                edges[var.name] = np.linspace(
                    lims[var]["min"], lims[var]["max"], npts[var.id]
                )
            elif var.name == "z":
                # Strech factor. TODO: allow parameters to be passed to mesh
                alpha = 2.3
                ii = np.array(range(0, npts[var.id]))
                a = lims[var]["min"]
                b = lims[var]["max"]
                edges[var.name] = (b - a) * (
                    np.exp(-alpha * ii / (npts[var.id] - 1)) - 1
                ) / (np.exp(-alpha) - 1) + a

        super().__init__(edges, coord_sys, tabs)


class ScikitChebyshev2DSubMesh(ScikitSubMesh2D):
    """
    Contains information about the 2D finite element mesh generated by taking the
    tensor product of two 1D meshes which use Chebyshev nodes on the
    interval (a, b), given by

    .. math::
        x_{k} = \\frac{1}{2}(a+b) + \\frac{1}{2}(b-a) \\cos(\\frac{2k-1}{2N}\\pi),

    for k = 1, ..., N, where N is the number of nodes. Note: this mesh then
    appends the boundary edgess, so that the 1D mesh edges are given by

    .. math ::
        a < x_{1} < ... < x_{N} < b.

    Note: This class only allows for the use of piecewise-linear triangular
    finite elements.

    Parameters
    ----------
    lims : dict
        A dictionary that contains the limits of each
        spatial variable
    npts : dict
        A dictionary that contains the number of points to be used on each
        spatial variable
    tabs : dict
        A dictionary that contains information about the size and location of
        the tabs
    """

    def __init__(self, lims, npts, tabs):

        # check that two variables have been passed in
        if len(lims) != 2:
            raise pybamm.GeometryError(
                "lims should contain exactly two variables, not {}".format(len(lims))
            )

        # get spatial variables
        spatial_vars = list(lims.keys())

        # check coordinate system agrees
        if spatial_vars[0].coord_sys == spatial_vars[1].coord_sys:
            coord_sys = spatial_vars[0].coord_sys
        else:
            raise pybamm.DomainError(
                """spatial variables should have the same coordinate system,
                but have coordinate systems {} and {}""".format(
                    spatial_vars[0].coord_sys, spatial_vars[1].coord_sys
                )
            )

        # compute edges
        edges = {}
        for var in spatial_vars:
            if var.name not in ["y", "z"]:
                raise pybamm.DomainError(
                    "spatial variable must be y or z not {}".format(var.name)
                )
            else:
                # Create N Chebyshev nodes in the interval (a,b)
                N = npts[var.id] - 2
                ii = np.array(range(1, N + 1))
                a = lims[var]["min"]
                b = lims[var]["max"]
                x_cheb = (a + b) / 2 + (b - a) / 2 * np.cos(
                    (2 * ii - 1) * np.pi / 2 / N
                )

                # Append the boundary nodes. Note: we need to flip the order the
                # Chebyshev nodes as they are created in descending order.
                edges[var.name] = np.concatenate(([a], np.flip(x_cheb), [b]))

        super().__init__(edges, coord_sys, tabs)


class UserSupplied2DSubMesh(ScikitSubMesh2D):
    """
    A class to generate a tensor product submesh on a 2D domain by using two user
    supplied vectors of edges: one for the y-direction and one for the z-direction.
    Note: this mesh should be created using :class:`GetUserSupplied2DSubMesh`.

    Parameters
    ----------
    lims : dict
        A dictionary that contains the limits of the spatial variables
    npts : dict
        A dictionary that contains the number of points to be used on each
        spatial variable. Note: the number of nodes (located at the cell centres)
        is npts, and the number of edges is npts+1.
    tabs : dict
        A dictionary that contains information about the size and location of
        the tabs
    y_edges : array_like
        The array of points which correspond to the edges in the y direction
        of the mesh.
    z_edges : array_like
        The array of points which correspond to the edges in the z direction
        of the mesh.
    """

    def __init__(self, lims, npts, tabs, y_edges, z_edges):

        # check that two variables have been passed in
        if len(lims) != 2:
            raise pybamm.GeometryError(
                "lims should contain exactly two variables, not {}".format(len(lims))
            )

        # get spatial variables
        spatial_vars = list(lims.keys())

        # check coordinate system agrees
        if spatial_vars[0].coord_sys == spatial_vars[1].coord_sys:
            coord_sys = spatial_vars[0].coord_sys
        else:
            raise pybamm.DomainError(
                """spatial variables should have the same coordinate system,
                but have coordinate systems {} and {}""".format(
                    spatial_vars[0].coord_sys, spatial_vars[1].coord_sys
                )
            )

        # check and store edges
        edges = {"y": y_edges, "z": z_edges}
        for var in spatial_vars:

            # check that npts equals number of user-supplied edges
            if npts[var.id] != len(edges[var.name]):
                raise pybamm.GeometryError(
                    """User-suppled edges has should have length npts but has length {}.
                     Number of points (npts) for variable {} in
                     domain {} is {}.""".format(
                        len(edges[var.name]), var.name, var.domain, npts[var.id]
                    )
                )

            # check end points of edges agree with spatial_lims
            if edges[var.name][0] != lims[var]["min"]:
                raise pybamm.GeometryError(
                    """First entry of edges is {}, but should be equal to {}
                     for variable {} in domain {}.""".format(
                        edges[var.name][0], lims[var]["min"], var.name, var.domain
                    )
                )
            if edges[var.name][-1] != lims[var]["max"]:
                raise pybamm.GeometryError(
                    """Last entry of edges is {}, but should be equal to {}
                    for variable {} in domain {}.""".format(
                        edges[var.name][-1], lims[var]["max"], var.name, var.domain
                    )
                )

        super().__init__(edges, coord_sys=coord_sys, tabs=tabs)


class GetUserSupplied2DSubMesh:
    """
    A class to generate a tensor product submesh on a 2D domain by using two user
    supplied vectors of edges: one for the y-direction and one for the z-direction.

    Parameters
    ----------
    y_edges : array_like
        The array of points which correspond to the edges in the y direction
        of the mesh.
    z_edges : array_like
        The array of points which correspond to the edges in the z direction
        of the mesh.
    """

    def __init__(self, y_edges, z_edges):
        self.y_edges = y_edges
        self.z_edges = z_edges

    def __call__(self, lims, npts, tabs=None):
        return UserSupplied2DSubMesh(lims, npts, tabs, self.y_edges, self.z_edges)
