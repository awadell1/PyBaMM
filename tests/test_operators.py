from pybat_lead_acid.parameters import Parameters
from pybat_lead_acid.mesh import Mesh, UniformMesh
from pybat_lead_acid.spatial_operators import *

import numpy as np
import unittest

class TestOperators(unittest.TestCase):

    def setUp(self):
        # Generate parameters and grids
        self.param = Parameters()

    def tearDown(self):
        del self.param

    def test_grad_div_1D_FV_basic(self):
        mesh = Mesh(self.param, 50)
        y = np.ones_like(mesh.xc)
        N = np.ones_like(mesh.x)

        # Get operators
        grad, div = get_spatial_operators("Finite Volumes", mesh)

        # Check output shape
        self.assertEqual(grad(y).shape[0], y.shape[0] - 1)
        self.assertEqual(div(N).shape[0], N.shape[0] - 1)

        # Check grad and div are both zero
        self.assertEqual(np.linalg.norm(grad(y)), 0)
        self.assertEqual(np.linalg.norm(div(N)), 0)

    def test_grad_div_1D_FV_convergence(self):
        # Convergence
        ns = [50, 100, 200]
        grad_errs = [0]*len(ns)
        div_errs = [0]*len(ns)
        for i, n in enumerate(ns):
            # Define problem and exact solutions
            mesh = Mesh(self.param, n)
            y = np.sin(mesh.xc)
            grad_y_exact = np.cos(mesh.x[1:-1])
            div_exact = - np.sin(mesh.xc)

            # Get operators and flux
            grad, div = get_spatial_operators("Finite Volumes", mesh)
            grad_y_approx = grad(y)

            # Calculate divergence of exact flux to avoid double errors
            # (test for those separately)
            N_exact = np.cos(mesh.x)
            div_approx = div(N_exact)

            # Calculate errors
            grad_errs[i] = (np.linalg.norm(grad_y_approx-grad_y_exact)
                            /np.linalg.norm(grad_y_exact))
            div_errs[i] = (np.linalg.norm(div_approx-div_exact)
                            /np.linalg.norm(div_exact))

        # Expect h**2 convergence
        [self.assertLess(grad_errs[i+1]/grad_errs[i], 0.26)
         for i in range(len(grad_errs)-1)]
        [self.assertLess(div_errs[i+1]/div_errs[i], 0.26)
         for i in range(len(div_errs)-1)]


if __name__ == '__main__':
    unittest.main()
