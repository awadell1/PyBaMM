#
# Write a symbol to Julia
#
import pybamm

import numpy as np
import scipy.sparse
from collections import OrderedDict
from pybamm.util import is_constant_and_can_evaluate

import numbers


def id_to_julia_variable(symbol_id, prefix):
    """
    This function defines the format for the julia variable names used in find_symbols
    and to_julia. Variable names are based on a nodes' id to make them unique
    """
    var_format = prefix + "_{:05d}"
    # Need to replace "-" character to make them valid julia variable names
    return var_format.format(symbol_id).replace("-", "m")


def find_symbols(
    symbol,
    constant_symbols,
    variable_symbols,
    variable_symbol_sizes,
    round_constants=True,
):
    """
    This function converts an expression tree to a dictionary of node id's and strings
    specifying valid julia code to calculate that nodes value, given y and t.

    The function distinguishes between nodes that represent constant nodes in the tree
    (e.g. a pybamm.Matrix), and those that are variable (e.g. subtrees that contain
    pybamm.StateVector). The former are put in `constant_symbols`, the latter in
    `variable_symbols`

    Note that it is important that the arguments `constant_symbols` and
    `variable_symbols` be and *ordered* dict, since the final ordering of the code lines
    are important for the calculations. A dict is specified rather than a list so that
    identical subtrees (which give identical id's) are not recalculated in the code

    Parameters
    ----------
    symbol : :class:`pybamm.Symbol`
        The symbol or expression tree to convert

    constant_symbol : collections.OrderedDict
        The output dictionary of constant symbol ids to lines of code

    variable_symbol : collections.OrderedDict
        The output dictionary of variable (with y or t) symbol ids to lines of code

    variable_symbol_sizes : collections.OrderedDict
        The output dictionary of variable (with y or t) symbol ids to size of that
        variable, for caching

    """
    if is_constant_and_can_evaluate(symbol):
        value = symbol.evaluate()
        if round_constants:
            value = np.round(value, decimals=11)
        if not isinstance(value, numbers.Number):
            if scipy.sparse.issparse(value):
                # Create Julia SparseArray
                row, col, data = scipy.sparse.find(value)
                if round_constants:
                    data = np.round(data, decimals=11)
                m, n = value.shape
                # Set print options large enough to avoid ellipsis
                # at least as big is len(row) = len(col) = len(data)
                np.set_printoptions(
                    threshold=max(np.get_printoptions()["threshold"], len(row) + 10)
                )
                # increase precision for printing
                np.set_printoptions(precision=20)
                # add 1 to correct for 1-indexing in Julia
                # use array2string so that commas are included
                constant_symbols[symbol.id] = "sparse({}, {}, {}, {}, {})".format(
                    np.array2string(row + 1, separator=","),
                    np.array2string(col + 1, separator=","),
                    np.array2string(data, separator=","),
                    m,
                    n,
                )
            elif value.shape == (1, 1):
                # Extract value if array has only one entry
                constant_symbols[symbol.id] = value[0, 0]
                variable_symbol_sizes[symbol.id] = 1
            elif value.shape[1] == 1:
                # Set print options large enough to avoid ellipsis
                # at least as big as len(row) = len(col) = len(data)
                np.set_printoptions(
                    threshold=max(
                        np.get_printoptions()["threshold"], value.shape[0] + 10
                    )
                )
                # Flatten a 1D array
                constant_symbols[symbol.id] = np.array2string(
                    value.flatten(), separator=","
                )
                variable_symbol_sizes[symbol.id] = symbol.shape[0]
            else:
                constant_symbols[symbol.id] = value
                # No need to save the size as it will not need to be used
        return

    # process children recursively
    for child in symbol.children:
        find_symbols(
            child,
            constant_symbols,
            variable_symbols,
            variable_symbol_sizes,
            round_constants=round_constants,
        )

    # calculate the variable names that will hold the result of calculating the
    # children variables
    children_vars = []
    for child in symbol.children:
        if is_constant_and_can_evaluate(child):
            child_eval = child.evaluate()
            if isinstance(child_eval, numbers.Number):
                children_vars.append(str(child_eval))
            else:
                children_vars.append(id_to_julia_variable(child.id, "const"))
        else:
            children_vars.append(id_to_julia_variable(child.id, "cache"))

    if isinstance(symbol, pybamm.BinaryOperator):
        # TODO: we can pass through a dummy y and t to get the type and then hardcode
        # the right line, avoiding these checks
        if isinstance(symbol, pybamm.MatrixMultiplication):
            symbol_str = "{0} @ {1}".format(children_vars[0], children_vars[1])
        elif isinstance(symbol, pybamm.Inner):
            symbol_str = "{0} * {1}".format(children_vars[0], children_vars[1])
        elif isinstance(symbol, pybamm.Minimum):
            symbol_str = "min({},{})".format(children_vars[0], children_vars[1])
        elif isinstance(symbol, pybamm.Maximum):
            symbol_str = "max({},{})".format(children_vars[0], children_vars[1])
        elif isinstance(symbol, pybamm.Power):
            # julia uses ^ instead of ** for power
            # include dot for elementwise operations
            symbol_str = children_vars[0] + " .^ " + children_vars[1]
        else:
            # all other operations use the same symbol
            symbol_str = children_vars[0] + " " + symbol.name + " " + children_vars[1]

    elif isinstance(symbol, pybamm.UnaryOperator):
        # Index has a different syntax than other univariate operations
        if isinstance(symbol, pybamm.Index):
            # Because of how julia indexing works, add 1 to the start, but not to the
            # stop
            symbol_str = "{}[{}:{}]".format(
                children_vars[0], symbol.slice.start + 1, symbol.slice.stop
            )
        else:
            symbol_str = symbol.name + children_vars[0]

    elif isinstance(symbol, pybamm.Function):
        # write functions directly
        symbol_str = "{}({})".format(symbol.julia_name, ", ".join(children_vars))

    elif isinstance(symbol, pybamm.Concatenation):
        if isinstance(symbol, (pybamm.NumpyConcatenation, pybamm.SparseStack)):
            # return a list of the children variables, which will be converted to a
            # line by line assignment
            # return this as a string so that other functionality still works
            # also save sizes
            symbol_str = "["
            for child in children_vars:
                child_id = child[6:].replace("m", "-")
                size = variable_symbol_sizes[int(child_id)]
                symbol_str += "{}::{}, ".format(size, child)
            symbol_str = symbol_str[:-2] + "]"

        # DomainConcatenation specifies a particular ordering for the concatenation,
        # which we must follow
        elif isinstance(symbol, pybamm.DomainConcatenation):
            if symbol.secondary_dimensions_npts == 1:
                all_child_vectors = children_vars
                all_child_sizes = [
                    variable_symbol_sizes[int(child[6:].replace("m", "-"))]
                    for child in children_vars
                ]
            else:
                slice_starts = []
                all_child_vectors = []
                all_child_sizes = []
                for i in range(symbol.secondary_dimensions_npts):
                    child_vectors = []
                    child_sizes = []
                    for child_var, slices in zip(
                        children_vars, symbol._children_slices
                    ):
                        for child_dom, child_slice in slices.items():
                            slice_starts.append(symbol._slices[child_dom][i].start)
                            # add 1 to slice start to account for julia indexing
                            child_vectors.append(
                                "@view {}[{}:{}]".format(
                                    child_var,
                                    child_slice[i].start + 1,
                                    child_slice[i].stop,
                                )
                            )
                            child_sizes.append(
                                child_slice[i].stop - child_slice[i].start
                            )
                    all_child_vectors.extend(
                        [v for _, v in sorted(zip(slice_starts, child_vectors))]
                    )
                    all_child_sizes.extend(
                        [v for _, v in sorted(zip(slice_starts, child_sizes))]
                    )
            # return a list of the children variables, which will be converted to a
            # line by line assignment
            # return this as a string so that other functionality still works
            # also save sizes
            symbol_str = "["
            for child, size in zip(all_child_vectors, all_child_sizes):
                symbol_str += "{}::{}, ".format(size, child)
            symbol_str = symbol_str[:-2] + "]"

    # Note: we assume that y is being passed as a column vector
    elif isinstance(symbol, pybamm.StateVectorBase):
        if isinstance(symbol, pybamm.StateVector):
            name = "@view y"
        elif isinstance(symbol, pybamm.StateVectorDot):
            name = "@view dy"
        indices = np.argwhere(symbol.evaluation_array).reshape(-1).astype(np.int32)
        # add 1 since julia uses 1-indexing
        indices += 1
        if len(indices) == 1:
            symbol_str = "{}[{}]".format(name, indices[0])
        else:
            # julia does include the final value
            symbol_str = "{}[{}:{}]".format(name, indices[0], indices[-1])

    elif isinstance(symbol, pybamm.Time):
        symbol_str = "t"

    elif isinstance(symbol, pybamm.InputParameter):
        symbol_str = "inputs['{}']".format(symbol.name)

    else:
        raise NotImplementedError(
            "Conversion to Julia not implemented for a symbol of type '{}'".format(
                type(symbol)
            )
        )

    variable_symbols[symbol.id] = symbol_str

    # Save the size of the symbol
    if symbol.shape == ():
        variable_symbol_sizes[symbol.id] = 1
    else:
        variable_symbol_sizes[symbol.id] = symbol.shape[0]


def to_julia(symbol, round_constants=True):
    """
    This function converts an expression tree into a dict of constant input values, and
    valid julia code that acts like the tree's :func:`pybamm.Symbol.evaluate` function

    Parameters
    ----------
    symbol : :class:`pybamm.Symbol`
        The symbol to convert to julia code

    Returns
    -------
    constant_values : collections.OrderedDict
        dict mapping node id to a constant value. Represents all the constant nodes in
        the expression tree
    str
        valid julia code that will evaluate all the variable nodes in the tree.

    """

    constant_values = OrderedDict()
    variable_symbols = OrderedDict()
    variable_symbol_sizes = OrderedDict()
    find_symbols(
        symbol,
        constant_values,
        variable_symbols,
        variable_symbol_sizes,
        round_constants=round_constants,
    )

    return constant_values, variable_symbols, variable_symbol_sizes


def get_julia_function(
    symbol,
    funcname="f",
    input_parameter_order=None,
    len_rhs=None,
    preallocate=True,
    round_constants=True,
):
    """
    Converts a pybamm expression tree into pure julia code that will calculate the
    result of calling `evaluate(t, y)` on the given expression tree.

    Parameters
    ----------
    symbol : :class:`pybamm.Symbol`
        The symbol to convert to julia code
    funcname : str, optional
        The name to give to the function (default 'f')
    input_parameter_order : list, optional
        List of input parameter names. Defines the order in which the input parameters
        are extracted from 'p' in the julia function that is created
    len_rhs : int, optional
        The number of ODEs in the discretized differential equations. This also
        determines whether the model has any algebraic equations: if None (default),
        the model is assume to have no algebraic parts and ``julia_str`` is compatible
        with an ODE solver. If not None, ``julia_str`` is compatible with a DAE solver
    preallocate : bool, optional
        Whether to write the function in a way that preallocates memory for the output.
        Default is True, which is faster. Must be False for the function to be
        modelingtoolkitized.

    Returns
    -------
    julia_str : str
        String of julia code, to be evaluated by ``julia.Main.eval``

    """
    if len_rhs is None:
        typ = "ode"
    else:
        typ = "dae"
        # Take away dy from the differential states
        # we will return a function of the form
        # out[] = .. - dy[] for the differential states
        # out[] = .. for the algebraic states
        symbol_minus_dy = []
        end = 0
        for child in symbol.orphans:
            start = end
            end += child.size
            if end <= len_rhs:
                symbol_minus_dy.append(child - pybamm.StateVectorDot(slice(start, end)))
            else:
                symbol_minus_dy.append(child)
        symbol = pybamm.numpy_concatenation(*symbol_minus_dy)
    constants, var_symbols, var_symbol_sizes = to_julia(
        symbol, round_constants=round_constants
    )

    # extract constants in generated function
    const_and_cache_str = "cs = (\n"
    shorter_const_names = {}
    for i_const, (symbol_id, const_value) in enumerate(constants.items()):
        const_name = id_to_julia_variable(symbol_id, "const")
        const_name_short = "const_{}".format(i_const)
        const_and_cache_str += "   {} = {},\n".format(const_name_short, const_value)
        shorter_const_names[const_name] = const_name_short

    # Pop (get and remove) items from the dictionary of symbols one by one
    # If they are simple operations (@view, +, -, *, /), replace all future
    # occurences instead of assigning them. This "inlining" speeds up the computation
    inlineable_symbols = ["@view", "+", "-", "*", "/"]
    var_str = ""
    input_parameters = {}
    while var_symbols:
        var_symbol_id, symbol_line = var_symbols.popitem(last=False)
        julia_var = id_to_julia_variable(var_symbol_id, "cache")
        # Look for lists in the variable symbols. These correpsond to concatenations, so
        # assign the children to the right parts of the vector
        if symbol_line[0] == "[" and symbol_line[-1] == "]":
            # convert to actual list
            symbol_line = symbol_line[1:-1].split(", ")
            start = 0
            if preallocate is True or var_symbol_id == symbol.id:
                for child_size_and_name in symbol_line:
                    child_size, child_name = child_size_and_name.split("::")
                    end = start + int(child_size)
                    # add 1 to start to account for julia 1-indexing
                    var_str += "@. {}[{}:{}] = {}\n".format(
                        julia_var, start + 1, end, child_name
                    )
                    start = end
            else:
                concat_str = "{} = vcat(".format(julia_var)
                for i, child_size_and_name in enumerate(symbol_line):
                    child_size, child_name = child_size_and_name.split("::")
                    var_str += "x{} = @. {}\n".format(i + 1, child_name)
                    concat_str += "x{}, ".format(i + 1)
                var_str += concat_str[:-2] + ")\n"
        # use mul! for matrix multiplications (requires LinearAlgebra library)
        elif " @ " in symbol_line:
            if preallocate is False:
                symbol_line = symbol_line.replace(" @ ", " * ")
                var_str += "{} = {}\n".format(julia_var, symbol_line)
            else:
                symbol_line = symbol_line.replace(" @ ", ", ")
                var_str += "mul!({}, {})\n".format(julia_var, symbol_line)
        # find input parameters
        elif symbol_line.startswith("inputs"):
            input_parameters[julia_var] = symbol_line[8:-2]
        elif "minimum" in symbol_line or "maximum" in symbol_line:
            var_str += "{} .= {}\n".format(julia_var, symbol_line)
        else:
            # don't replace the matrix multiplication cases (which will be
            # turned into a mul!), since it is faster to assign to a cache array
            # first in that case
            # e.g. mul!(cs.cache_1, cs.cache_2, cs.cache_3)
            # unless it is a @view in which case we don't
            # need to cache
            # e.g. mul!(cs.cache_1, cs.cache_2, @view y[1:10])
            # also don't replace the minimum() or maximum() cases as we can't
            # broadcast them
            any_matmul_min_max = any(
                julia_var in next_symbol_line
                and (
                    any(
                        x in next_symbol_line
                        for x in [" @ ", "mul!", "minimum", "maximum"]
                    )
                    and not symbol_line.startswith("@view")
                )
                for next_symbol_line in var_symbols.values()
            )
            # inline operation if it can be inlined
            if (
                any(x in symbol_line for x in inlineable_symbols) or symbol_line == "t"
            ) and not any_matmul_min_max:
                found_replacement = False
                # replace all other occurrences of the variable
                # in the dictionary with the symbol line
                for next_var_id, next_symbol_line in var_symbols.items():
                    if julia_var in next_symbol_line:
                        if symbol_line == "t":
                            # no brackets needed
                            var_symbols[next_var_id] = next_symbol_line.replace(
                                julia_var, symbol_line
                            )
                        else:
                            # add brackets so that the order of operations is maintained
                            var_symbols[next_var_id] = next_symbol_line.replace(
                                julia_var, "({})".format(symbol_line)
                            )
                        found_replacement = True
                if not found_replacement:
                    var_str += "@. {} = {}\n".format(julia_var, symbol_line)

            # otherwise assign
            else:
                var_str += "@. {} = {}\n".format(julia_var, symbol_line)
    # Replace all input parameter names
    for input_parameter_id, input_parameter_name in input_parameters.items():
        var_str = var_str.replace(input_parameter_id, input_parameter_name)

    # indent code
    var_str = "   " + var_str
    var_str = var_str.replace("\n", "\n   ")

    # add the cache variables to the cache NamedTuple
    i_cache = 0
    for var_symbol_id, var_symbol_size in var_symbol_sizes.items():
        # Skip caching the result variable since this is provided as dy
        # Also skip caching the result variable if it doesn't appear in the var_str,
        # since it has been inlined and does not need to be assigned to
        julia_var = id_to_julia_variable(var_symbol_id, "cache")
        if var_symbol_id != symbol.id and julia_var in var_str:
            julia_var_short = "cache_{}".format(i_cache)
            var_str = var_str.replace(julia_var, julia_var_short)
            i_cache += 1
            if preallocate is True:
                const_and_cache_str += "   {} = zeros({}),\n".format(
                    julia_var_short, var_symbol_size
                )
            else:
                # Cache variables have not been preallocated
                var_str = var_str.replace(
                    "@. {} = ".format(julia_var_short),
                    "{} = @. ".format(julia_var_short),
                )

    # Shorten the name of the constants from id to const_0, const_1, etc.
    for long, short in shorter_const_names.items():
        var_str = var_str.replace(long, "cs." + short)

    # close the constants and cache string
    const_and_cache_str += ")\n"

    # remove the constant and cache sring if it is empty
    const_and_cache_str = const_and_cache_str.replace("cs = (\n)\n", "")

    # calculate the final variable that will output the result
    if symbol.is_constant():
        result_var = id_to_julia_variable(symbol.id, "const")
        if result_var in shorter_const_names:
            result_var = shorter_const_names[result_var]
        result_value = symbol.evaluate()
        if isinstance(result_value, numbers.Number):
            var_str = var_str + "\n   dy .= " + str(result_value) + "\n"
        else:
            var_str = var_str + "\n   dy .= cs." + result_var + "\n"
    else:
        result_var = id_to_julia_variable(symbol.id, "cache")
        if typ == "ode":
            out = "dy"
        elif typ == "dae":
            out = "out"
        # replace "cache_123 = ..." with "dy .= ..." (ensure we allocate to the
        # variable that was passed in)
        var_str = var_str.replace(f"   {result_var} =", f"   {out} .=")
        # catch other cases for dy
        var_str = var_str.replace(result_var, out)

    # add "cs." to cache names
    if preallocate is True:
        var_str = var_str.replace("cache", "cs.cache")

    # line that extracts the input parameters in the right order
    if input_parameter_order is None:
        input_parameter_extraction = ""
    elif len(input_parameter_order) == 1:
        # extract the single parameter
        input_parameter_extraction = "   " + input_parameter_order[0] + " = p[1]\n"
    else:
        # extract all parameters
        input_parameter_extraction = "   " + ", ".join(input_parameter_order) + " = p\n"

    if preallocate is False or const_and_cache_str == "":
        func_def = f"{funcname}!"
    else:
        func_def = f"{funcname}_with_consts!"

    # add function def
    if typ == "ode":
        function_def = f"\nfunction {func_def}(dy, y, p, t)\n"
    elif typ == "dae":
        function_def = f"\nfunction {func_def}(out, dy, y, p, t)\n"
    julia_str = (
        "begin\n"
        + const_and_cache_str
        + function_def
        + input_parameter_extraction
        + var_str
    )

    # close the function, with a 'nothing' to avoid allocations
    julia_str += "nothing\nend\n\n"
    julia_str = julia_str.replace("\n   \n", "\n")

    if not (preallocate is False or const_and_cache_str == ""):
        # Use a let block for the cached variables
        # open the let block
        julia_str = julia_str.replace("cs = (", f"{funcname}! = let cs = (")
        # close the let block
        julia_str += "end\n"

    # close the "begin"
    julia_str += "end"

    return julia_str
